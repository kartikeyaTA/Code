"""
FastAPI backend for Azure AI Foundry Agent chat using local Azure CLI credentials.
Run: uvicorn backend:app --reload --port 8001
"""

import os
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

# ---------------------------------------------------------------------
# Load environment variables
# ---------------------------------------------------------------------
load_dotenv()
PROJECT_ENDPOINT = os.environ.get(
    "PROJECT_ENDPOINT",
    "https://voice-open-ai-txrh.services.ai.azure.com/api/projects/voice-open-ai-txrh",
)
AGENT_ID = os.environ.get("AGENT_ID", "hyb")

# ---------------------------------------------------------------------
# FastAPI app setup
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
# Azure AI Client Initialization (Uses DefaultAzureCredential / az login)
# ---------------------------------------------------------------------
credential = DefaultAzureCredential()

project_client = AIProjectClient(
    endpoint=PROJECT_ENDPOINT,
    credential=credential,
)
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


class SingleChatRequest(BaseModel):
    user_query: str


# ---------------------------------------------------------------------
# Create Conversation
# ---------------------------------------------------------------------
@app.post("/conversations", response_model=CreateConversationResponse)
def create_conversation():
    try:
        conversation = openai_client.conversations.create()
        print(f"Conversation created: {conversation.id}")
        return CreateConversationResponse(conversation_id=conversation.id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create conversation: {str(e)}",
        )


# ---------------------------------------------------------------------
# Chat (Existing - using existing conversation_id)
# ---------------------------------------------------------------------
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        response = openai_client.responses.create(
            conversation=req.conversation_id,
            input=req.user_query,
            extra_body={
                "agent_reference": {"name": AGENT_ID, "type": "agent_reference"}
            },
        )

        if response.status == "failed":
            raise HTTPException(
                status_code=502,
                detail=f"Agent execution failed: {response.last_error}",
            )

        print(f"User query: {req.user_query}")
        print(f"Agent reply: {response.output_text}")

        return ChatResponse(
            conversation_id=req.conversation_id,
            reply=response.output_text,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------
# Single-Call Chat (Creates conversation + sends query in one request)
# ---------------------------------------------------------------------
@app.post("/chat/single", response_model=ChatResponse)
def chat_single(req: SingleChatRequest):
    try:
        # Step 1: Create conversation
        conversation = openai_client.conversations.create()
        print(f"Conversation created: {conversation.id}")

        # Step 2: Send query using new conversation ID
        response = openai_client.responses.create(
            conversation=conversation.id,
            input=req.user_query,
            extra_body={
                "agent_reference": {"name": AGENT_ID, "type": "agent_reference"}
            },
        )

        if response.status == "failed":
            raise HTTPException(
                status_code=502,
                detail=f"Agent execution failed: {response.last_error}",
            )

        print(f"User query: {req.user_query}")
        print(f"Agent reply: {response.output_text}")

        return ChatResponse(
            conversation_id=conversation.id,
            reply=response.output_text,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok"}
