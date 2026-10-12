"""
FastAPI backend for Azure AI Foundry Agent chat with 80/20 version routing.
Run: uvicorn backend:app --reload --port 8001
"""

import hashlib
import logging
import os
import threading
import time

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("agent-router")

# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------
load_dotenv()
PROJECT_ENDPOINT = os.environ.get(
    "PROJECT_ENDPOINT",
    "https://voice-open-ai-txrh.services.ai.azure.com/api/projects/voice-open-ai-txrh",
)
AGENT_ID = os.environ.get("AGENT_ID", "hyb")
LATEST_PERCENT = int(os.environ.get("LATEST_PERCENT", "20"))        # latest = 20%, latest-1 = 80%
VERSION_REFRESH_SECONDS = int(os.environ.get("VERSION_REFRESH_SECONDS", "300"))

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

credential = DefaultAzureCredential()
project_client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
openai_client = project_client.get_openai_client()


# ---------------------------------------------------------------------
# Version router (latest = 20%, latest-1 = 80%)
# ---------------------------------------------------------------------
class VersionRouter:
    def __init__(self):
        self._latest = None
        self._previous = None
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def _discover(self):
        versions = [
            v for v in project_client.agents.list_versions(agent_name=AGENT_ID)
            if getattr(v, "status", "active") in (None, "active")
            and not getattr(v, "draft", False)
        ]
        versions.sort(key=lambda v: int(v.version))
        if not versions:
            raise RuntimeError(f"No active versions found for agent '{AGENT_ID}'")
        latest = str(versions[-1].version)
        previous = str(versions[-2].version) if len(versions) > 1 else latest
        return latest, previous

    def versions(self):
        if self._latest is not None and time.time() - self._loaded_at <= VERSION_REFRESH_SECONDS:
            return self._latest, self._previous
        with self._lock:
            if self._latest is None or time.time() - self._loaded_at > VERSION_REFRESH_SECONDS:
                try:
                    latest, previous = self._discover()
                    if (latest, previous) != (self._latest, self._previous):
                        log.info("Versions resolved: latest=%s previous=%s", latest, previous)
                    self._latest, self._previous = latest, previous
                    self._loaded_at = time.time()
                except Exception:
                    if self._latest is None:
                        raise
                    log.exception("Version refresh failed; keeping last known versions")
        return self._latest, self._previous

    @staticmethod
    def _bucket(key: str) -> int:
        return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 100

    def pick(self, sticky_key: str) -> str:
        latest, previous = self.versions()
        return latest if self._bucket(sticky_key) < LATEST_PERCENT else previous


router = VersionRouter()


def call_agent(conversation_id: str, user_query: str, sticky_key: str):
    """Calls the agent on the picked version; falls back to latest-1 if latest fails."""
    latest, previous = router.versions()
    version = router.pick(sticky_key)

    def _call(v: str):
        resp = openai_client.responses.create(
            conversation=conversation_id,
            input=user_query,
            extra_body={
                "agent_reference": {"name": AGENT_ID, "version": v, "type": "agent_reference"}
            },
        )
        if resp.status == "failed":
            raise RuntimeError(f"Agent execution failed: {getattr(resp, 'error', None)}")
        return resp

    try:
        resp = _call(version)
        log.info("conv=%s agent=%s version=%s", conversation_id, AGENT_ID, version)
        return resp, version
    except Exception as e:
        if version == latest and latest != previous:
            log.warning("Latest v%s failed (%s); falling back to v%s", latest, e, previous)
            resp = _call(previous)
            return resp, previous
        raise


# ---------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------
class CreateConversationResponse(BaseModel):
    conversation_id: str


class ChatRequest(BaseModel):
    conversation_id: str
    user_query: str


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    agent_version: str | None = None


class SingleChatRequest(BaseModel):
    user_query: str


# ---------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------
@app.post("/conversations", response_model=CreateConversationResponse)
def create_conversation():
    try:
        conversation = openai_client.conversations.create()
        return CreateConversationResponse(conversation_id=conversation.id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create conversation: {e}")


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        # sticky key = conversation_id -> same conversation always gets the same version
        response, version = call_agent(req.conversation_id, req.user_query, req.conversation_id)
        return ChatResponse(
            conversation_id=req.conversation_id,
            reply=response.output_text,
            agent_version=version,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/chat/single", response_model=ChatResponse)
def chat_single(req: SingleChatRequest):
    try:
        conversation = openai_client.conversations.create()
        response, version = call_agent(conversation.id, req.user_query, conversation.id)
        return ChatResponse(
            conversation_id=conversation.id,
            reply=response.output_text,
            agent_version=version,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/health")
def health():
    latest, previous = router.versions()
    return {"status": "ok", "latest": latest, "previous": previous, "latest_percent": LATEST_PERCENT}
