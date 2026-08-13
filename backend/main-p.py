"""
FastAPI backend for Azure AI Foundry Agent chat.
Run: uvicorn backend:app --reload --port 8001
"""

import os
import time
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from azure.core.credentials import AccessToken
from azure.ai.projects import AIProjectClient

# ---------------------------------------------------------------------
# Load environment variables
# ---------------------------------------------------------------------
load_dotenv()
PROJECT_ENDPOINT = os.environ.get("PROJECT_ENDPOINT", "your-project-endpoint")
AGENT_ID = os.environ.get("AGENT_ID", "your-agent-id")

# ---------------------------------------------------------------------
# FastAPI app & Security
# ---------------------------------------------------------------------
app = FastAPI(title="Azure AI Foundry Chat API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

# ---------------------------------------------------------------------
# Custom Azure SDK Credential
# ---------------------------------------------------------------------
class UserTokenCredential:
    """
    Wraps the raw Entra ID access token passed from the BFF.
    """
    def __init__(self, token: str):
        self.token = token

    def get_token(self, *scopes, **kwargs) -> AccessToken:
        return AccessToken(self.token, int(time.time()) + 3600)

# ---------------------------------------------------------------------
# Dependency Injection
# ---------------------------------------------------------------------
def get_user_openai_client(
    auth: HTTPAuthorizationCredentials = Depends(security),
    x_foundry_token: str = Header(...)
):
    """
    Validates backend API access, then builds the Azure client 
    using the Foundry token.
    """
    # Optional: You can validate auth.credentials (the backend_token) here
    # to ensure it's a valid JWT signed by your tenant before proceeding.
    backend_token = auth.credentials 

    user_credential = UserTokenCredential(x_foundry_token)
    project_client = AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=user_credential,
    )
    return project_client.get_openai_client()

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
def create_conversation(openai_client = Depends(get_user_openai_client)):
    try:
        conversation = openai_client.conversations.create()
        return CreateConversationResponse(conversation_id=conversation.id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create conversation: {str(e)}",
        )

# ---------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, openai_client = Depends(get_user_openai_client)):
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
            
        return ChatResponse(
            conversation_id=req.conversation_id,
            reply=response.output_text,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health():
    return {"status": "ok"}
