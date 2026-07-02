"""
Backend for the text-based chat app, backed by an agent deployed in
Microsoft Foundry (Azure AI Foundry Agent Service).

Run with:  uvicorn app:app --reload --port 8000
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import ListSortOrder

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

PROJECT_ENDPOINT = "https://private-test.services.ai.azure.com/api/projects/private-test-project"
AGENT_ID = "Agent"
SESSIONS_DIR = Path(__file__).parent / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Foundry Chat Backend")

# Allow the static frontend (served from a different port/origin) to call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://ca-chat-frontend-dev.politeocean-d2b5e0d5.eastus.azurecontainerapps.io"],  # tighten to your actual frontend origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



# One shared client for the process lifetime.
credential = DefaultAzureCredential()
project_client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
agent = project_client.agents.get(agent_name=AGENT_ID)
openai_client = project_client.get_openai_client()


# --------------------------------------------------------------------------
# Request / response models
# --------------------------------------------------------------------------
class ChatRequest(BaseModel):
    session_id: str
    user_query: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str


class SessionSummary(BaseModel):
    session_id: str
    title: str
    created_at: str


# --------------------------------------------------------------------------
# Local session storage helpers (sessions/<session_id>.json)
# --------------------------------------------------------------------------
def _session_path(session_id: str) -> Path:
    safe_id = session_id.replace("/", "_")
    return SESSIONS_DIR / f"{safe_id}.json"


def _load_session(session_id: str) -> dict:
    path = _session_path(session_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    return json.loads(path.read_text())


def _save_session(data: dict) -> None:
    _session_path(data["session_id"]).write_text(json.dumps(data, indent=2))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.get("/sessions", response_model=list[SessionSummary])
def list_sessions():
    """Return all sessions, most recently updated first, for the left pane."""
    sessions = []
    for f in SESSIONS_DIR.glob("*.json"):
        data = json.loads(f.read_text())
        sessions.append(
            SessionSummary(
                session_id=data["session_id"],
                title=data.get("title", "New chat"),
                created_at=data.get("created_at", ""),
            )
        )
    sessions.sort(key=lambda s: s.created_at, reverse=True)
    return sessions


@app.post("/sessions", response_model=SessionSummary)
def create_session():
    """Create a brand new, empty session (and a matching Foundry conversation)."""
    session_id = str(uuid.uuid4())

    # New SDK pattern uses conversations instead of threads
    conversation = openai_client.conversations.create()

    data = {
        "session_id": session_id,
        "thread_id": conversation.id,  # map conversation ID here
        "title": "New chat",
        "created_at": _now(),
        "messages": [],
    }
    _save_session(data)
    return SessionSummary(session_id=session_id, title=data["title"], created_at=data["created_at"])


@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    """Return the full conversation for a session, to render in the middle pane."""
    data = _load_session(session_id)
    return {
        "session_id": data["session_id"],
        "title": data.get("title", "New chat"),
        "messages": data.get("messages", []),
    }


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    path = _session_path(session_id)
    if path.exists():
        path.unlink()
    return {"deleted": True}

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """Send a user message to the Foundry agent and return its reply."""
    data = _load_session(req.session_id)
    conversation_id = data["thread_id"]

    # 1. Add the user's message to the conversation item sequence
    openai_client.conversations.items.create(
        conversation_id=conversation_id,
        items=[{"type": "message", "role": "user", "content": req.user_query}]
    )

    # 2. Call the agent response (handles execution automatically)
    response = openai_client.responses.create(
        conversation=conversation_id,
        extra_body={"agent_reference": {"name": agent.name, "type": "agent_reference"}},
    )

    if response.status == "failed":
        raise HTTPException(status_code=502, detail=f"Agent response failed: {response.last_error}")

    # 3. Read the generated reply text directly from the output text attribute
    reply_text = response.output_text if hasattr(response, "output_text") else ""

    # 4. Persist turns locally
    timestamp = _now()
    data["messages"].append({"role": "user", "content": req.user_query, "timestamp": timestamp})
    data["messages"].append({"role": "assistant", "content": reply_text, "timestamp": timestamp})

    if data["title"] == "New chat" and req.user_query.strip():
        data["title"] = req.user_query.strip()[:40]

    _save_session(data)

    return ChatResponse(session_id=req.session_id, reply=reply_text)


@app.get("/health")
def health():
    return {"status": "ok"}