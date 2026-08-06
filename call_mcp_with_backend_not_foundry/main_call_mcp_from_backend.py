"""
FastAPI backend for Azure AI Foundry Agent chat.

Endpoints:
1. POST /conversations -> Create a new conversation
2. POST /chat -> Send a message to an existing conversation

Run:
uvicorn main_call_mcp_from_backend:app --reload --port 8000
"""

import os
import json
import asyncio

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

# --- MCP Client Imports ---
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession

# ---------------------------------------------------------------------
# Load environment variables
# ---------------------------------------------------------------------
load_dotenv()

PROJECT_ENDPOINT = os.environ["PROJECT_ENDPOINT"]
AGENT_ID = "call-mcp-from-backend-agent"
MCP_SERVER_URL = "https://apim-gateway-application-test-dev-txrh-mcp.azure-api.net/sse/sse"
MCP_API_KEY = os.environ.get("MCP_API_KEY", "mcp_key")

print("AGENT_ID", AGENT_ID)

# ---------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------
app = FastAPI(title="Azure AI Foundry Chat API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------
# Azure AI Foundry clients
# ---------------------------------------------------------------------
credential = DefaultAzureCredential()

project_client = AIProjectClient(
    endpoint=PROJECT_ENDPOINT,
    credential=credential,
)

agent = project_client.agents.get(agent_name=AGENT_ID)
openai_client = project_client.get_openai_client()

# ---------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------
class CreateConversationResponse(BaseModel):
    conversation_id: str

class ChatRequest(BaseModel):
    conversation_id: str
    user_query: str

class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    tool_results: list = []

# ---------------------------------------------------------------------
# MCP Execution Helper
# ---------------------------------------------------------------------
async def execute_mcp_tool(tool_name: str, parameters: dict) -> dict:
    """Connects to the MCP SSE server, calls a tool, and returns the result."""
    headers = {"x-api-key": MCP_API_KEY,
               "my-snow-secret-key": "snow_key"}

    try:
        async with sse_client(url=MCP_SERVER_URL, headers=headers) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                result = await session.call_tool(tool_name, arguments=parameters)

                # Extract text content from the MCP tool result
                extracted_texts = [
                    content.text for content in result.content if content.type == "text"
                ]

                return {
                    "tool": tool_name,
                    "status": "success",
                    "output": extracted_texts
                }
    except Exception as e:
        print(f"Error executing MCP tool {tool_name}: {e}")
        return {
            "tool": tool_name,
            "status": "error",
            "output": str(e)
        }

# ---------------------------------------------------------------------
# Create Conversation
# ---------------------------------------------------------------------
@app.post("/conversations", response_model=CreateConversationResponse)
def create_conversation():
    try:
        conversation = openai_client.conversations.create()
        return CreateConversationResponse(conversation_id=conversation.id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create conversation: {str(e)}")

# ---------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------
@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        # Pass 1: Send the user's query to the Agent to get routing intent
        response = openai_client.responses.create(
            conversation=req.conversation_id,
            input=req.user_query,
            extra_body={
                "agent_reference": {"name": agent.name, "type": "agent_reference"}
            },
        )

        if response.status == "failed":
            raise HTTPException(status_code=502, detail=f"Agent execution failed: {response.last_error}")

        agent_output = response.output_text.strip()

        print("i am printing the agent output")
        print(agent_output)

        # Parse JSON
        try:
            parsed_intent = json.loads(agent_output)
            tools_to_call = parsed_intent.get("tools_to_call", [])
            is_json = True
        except json.JSONDecodeError:
            tools_to_call = []
            is_json = False

        tool_results = []
        final_reply = agent_output

        # If it's a valid routing JSON, execute the tools
        if is_json and tools_to_call:
            tasks = []
            for tool in tools_to_call:
                name = tool.get("name")
                params = tool.get("parameters", {})
                if name:
                    tasks.append(execute_mcp_tool(name, params))

            tool_results = await asyncio.gather(*tasks)

            # Pass 2: Check if KB was searched. If so, feed it back for a conversational answer.
            kb_results = [res for res in tool_results if res.get("tool") == "search_kb_via_table_api"]

            if kb_results:
                # Format the KB output nicely
                kb_context_str = json.dumps([kb.get("output") for kb in kb_results], indent=2)

                follow_up_input = (
                    f"User Query: {req.user_query}\n\n"
                    f"Knowledge Base Context:\n{kb_context_str}\n\n"
                    "Please provide a helpful response answering the user's query based ONLY on the provided context."
                )

                # Send the follow-up prompt to the same conversation
                second_response = openai_client.responses.create(
                    conversation=req.conversation_id,
                    input=follow_up_input,
                    extra_body={
                        "agent_reference": {"name": agent.name, "type": "agent_reference"}
                    },
                )

                final_reply = second_response.output_text.strip()
            else:
                # If it was an incident creation, we just return the tool execution status
                final_reply = "Tool execution completed."

        return ChatResponse(
            conversation_id=req.conversation_id,
            reply=final_reply,
            tool_results=tool_results
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health():
    return {"status": "ok"}