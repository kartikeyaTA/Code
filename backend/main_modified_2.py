"""
FastAPI backend for Azure AI Foundry Agent chat.

Endpoints:
1. POST /conversations -> Create a new conversation
2. POST /chat -> Send a message to an existing conversation

Run:
uvicorn app:app --reload --port 8000
"""

import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient


from fastapi.responses import StreamingResponse
# ---------------------------------------------------------------------
# Load environment variables
# ---------------------------------------------------------------------
load_dotenv()

PROJECT_ENDPOINT = os.environ["PROJECT_ENDPOINT"]
AGENT_ID = os.environ["AGENT_ID"]

print("AGENT_ID", AGENT_ID)

# ---------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------
app = FastAPI(title="Azure AI Foundry Chat API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
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


# ---------------------------------------------------------------------
# Create Conversation
# ---------------------------------------------------------------------
@app.post("/conversations", response_model=CreateConversationResponse)
def create_conversation():
    """
    Creates a new Foundry conversation.
    """

    try:
        conversation = openai_client.conversations.create()

        return CreateConversationResponse(
            conversation_id=conversation.id
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create conversation: {str(e)}",
        )


# ---------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """
    Sends a user message to an existing conversation
    and invokes the Foundry Agent.
    """

    try:

        response = openai_client.responses.create(
            conversation=req.conversation_id,
            input=req.user_query,
            extra_body={
                "agent_reference": {"name": agent.name, "type": "agent_reference"}
            },
        )


        # Submit another response after user consent
        # response = openai_client.responses.create(
        #     conversation=req.conversation_id,
        #     input=req.user_query,
        #     extra_body={
        #         "agent_reference": {"name": agent.name, "type": "agent_reference"},
        #         "tool_choice": "required",
        #         "stream": True
        #     },
        # )
        print(response)

        if response.status == "failed":
            raise HTTPException(
                status_code=502,
                detail=f"Agent execution failed: {response.last_error}",
            )

        return ChatResponse(
            conversation_id=req.conversation_id,
            reply=response.output_text,
        )

    except HTTPException:
        raise

    except Exception as e:
        print(e)
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )





# ... (Keep your existing imports, setup, and /conversations endpoint) ...

# ---------------------------------------------------------------------
# Chat (Streaming)
# ---------------------------------------------------------------------
# @app.post("/chat") # <-- 2. Remove response_model=ChatResponse
# def chat(req: ChatRequest):
#     """
#     Sends a user message and streams the Foundry Agent's response back.
#     """

#     def event_stream():
#         try:
#             # 3. Pass stream=True natively to the SDK, not in extra_body
#             response_stream = openai_client.responses.create(
#                 conversation=req.conversation_id,
#                 input=req.user_query,
#                 stream=True,
#                 extra_body={
#                     "agent_reference": {"name": agent.name, "type": "agent_reference"},
#                     "tool_choice": "required"
#                 },
#             )

#             # 4. Iterate over the stream as chunks arrive
#             for chunk in response_stream:
#                 # The exact attribute depends on the Azure AI Responses SDK,
#                 # but it typically yields chunks with delta text.
#                 if hasattr(chunk, "output_text") and chunk.output_text:
#                     # Yielding standard Server-Sent Events (SSE) format
#                     yield f"data: {chunk.output_text}\n\n"

#             # Close the stream explicitly
#             yield "data: [DONE]\n\n"

#         except Exception as e:
#             print(f"Streaming failed: {e}")
#             yield f"data: {{\"error\": \"{str(e)}\"}}\n\n"

#     # 5. Return the StreamingResponse
#     return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}