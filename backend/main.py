import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
PROJECT_ENDPOINT = os.getenv(
    "FOUNDRY_ENDPOINT",
    "https://foundry-services-applications4-dev.services.ai.azure.com/api/projects/foundry-project-applications4-dev"
).split("/openai/v1")[0].rstrip("/")

AGENT_ID = os.getenv("AgentVersion", "Agent")
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")

# Safe writable directory fallback for locked down Linux containers
if AZURE_CLIENT_ID or os.getenv("KUBERNETES_SERVICE_HOST"):
    SESSIONS_DIR = Path("/tmp/sessions")
else:
    SESSIONS_DIR = Path(__file__).parent / "sessions"

SESSIONS_DIR.mkdir(exist_ok=True)

# Define global placeholder clients
project_client = None
openai_client = None
agent = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Safely initializes clients on startup using the working identity logic"""
    global project_client, openai_client, agent
    
    # 🔑 FIX: Use the exact working identity binding strategy
    if AZURE_CLIENT_ID:
        print(f"Production Mode: Explicitly binding to Managed Identity: {AZURE_CLIENT_ID}")
        credential = DefaultAzureCredential(managed_identity_client_id=AZURE_CLIENT_ID)
    else:
        print("Development Mode: Falling back to local credentials...")
        credential = DefaultAzureCredential()

    try:
        # Initialize clients lazily inside startup lifespan rather than global block space
        project_client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
        openai_client = project_client.get_openai_client()
        agent = project_client.agents.get(agent_name=AGENT_ID)
        print("🚀 Azure AI Project Client initialized successfully!")
    except Exception as e:
        print(f"⚠️ Startup warning (Network/Auth block): {e}. App will remain online.")
        
    yield
    if project_client:
        project_client.close()

app = FastAPI(title="Foundry Chat Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    sessions = []
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            sessions.append(
                SessionSummary(
                    session_id=data["session_id"],
                    title=data.get("title", "New chat"),
                    created_at=data.get("created_at", ""),
                )
            )
        except Exception:
            continue
    sessions.sort(key=lambda s: s.created_at, reverse=True)
    return sessions

@app.post("/sessions", response_model=SessionSummary)
def create_session():
    if not openai_client:
        raise HTTPException(status_code=503, detail="AI SDK clients are not initialized due to infrastructure blocks.")
        
    session_id = str(uuid.uuid4())
    conversation = openai_client.conversations.create()

    data = {
        "session_id": session_id,
        "thread_id": conversation.id,
        "title": "New chat",
        "created_at": _now(),
        "messages": [],
    }
    _save_session(data)
    return SessionSummary(session_id=session_id, title=data["title"], created_at=data["created_at"])

@app.get("/sessions/{session_id}")
def get_session(session_id: str):
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
    if not openai_client or not agent:
        raise HTTPException(status_code=503, detail="AI Client connection uninitialized or blocked by VNet.")

    data = _load_session(req.session_id)
    conversation_id = data["thread_id"]

    openai_client.conversations.items.create(
        conversation_id=conversation_id,
        items=[{"type": "message", "role": "user", "content": req.user_query}]
    )

    response = openai_client.responses.create(
        conversation=conversation_id,
        extra_body={"agent_reference": {"name": agent.name, "type": "agent_reference"}},
    )

    if response.status == "failed":
        raise HTTPException(status_code=502, detail=f"Agent response failed: {response.last_error}")

    reply_text = response.output_text if hasattr(response, "output_text") else ""

    timestamp = _now()
    data["messages"].append({"role": "user", "content": req.user_query, "timestamp": timestamp})
    data["messages"].append({"role": "assistant", "content": reply_text, "timestamp": timestamp})

    if data["title"] == "New chat" and req.user_query.strip():
        data["title"] = req.user_query.strip()[:40]

    _save_session(data)
    return ChatResponse(session_id=req.session_id, reply=reply_text)

@app.get("/health")
def health():
    return {"status": "ok", "sdk_connected": openai_client is not None}