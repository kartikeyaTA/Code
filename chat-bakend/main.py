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

# PROJECT_ENDPOINT = os.environ["PROJECT_ENDPOINT"]
# AGENT_ID = os.environ["AGENT_ID"]
# AGENT_NAME = os.environ["AGENT_NAME"]

# print("AGENT_ID", AGENT_NAME)

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
# credential = DefaultAzureCredential()

# project_client = AIProjectClient(
#     endpoint=PROJECT_ENDPOINT,
#     credential=credential,
# )

# agent = project_client.agents.get(agent_name=AGENT_NAME)
# openai_client = project_client.get_openai_client()

# # ---------------------------------------------------------------------
# # Request / Response Models
# # ---------------------------------------------------------------------
# class CreateConversationResponse(BaseModel):
#     conversation_id: str


# class ChatRequest(BaseModel):
#     conversation_id: str
#     user_query: str


# class ChatResponse(BaseModel):
#     conversation_id: str
#     reply: str


# # ---------------------------------------------------------------------
# # Create Conversation
# # ---------------------------------------------------------------------
# @app.post("/conversations", response_model=CreateConversationResponse)
# def create_conversation():
#     """
#     Creates a new Foundry conversation.
#     """

#     try:
#         conversation = openai_client.conversations.create()

#         return CreateConversationResponse(
#             conversation_id=conversation.id
#         )

#     except Exception as e:
#         raise HTTPException(
#             status_code=500,
#             detail=f"Failed to create conversation: {str(e)}",
#         )


# # ---------------------------------------------------------------------
# # Chat
# # ---------------------------------------------------------------------
# @app.post("/chat", response_model=ChatResponse)
# def chat(req: ChatRequest):
#     """
#     Sends a user message to an existing conversation
#     and invokes the Foundry Agent.
#     """

#     try:

#         response = openai_client.responses.create(
#             conversation=req.conversation_id,
#             input=req.user_query,
#             extra_body={
#                 "agent_reference": {"name": agent.name, "type": "agent_reference"}
#             },
#         )

#         print(response)

#         if response.status == "failed":
#             raise HTTPException(
#                 status_code=502,
#                 detail=f"Agent execution failed: {response.last_error}",
#             )

#         return ChatResponse(
#             conversation_id=req.conversation_id,
#             reply=response.output_text,
#         )

#     except HTTPException:
#         raise

#     except Exception as e:
#         print(e)
#         raise HTTPException(
#             status_code=500,
#             detail=str(e),
#         )


# ---------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/connect")
def connect():
    return {"Connetion working"}