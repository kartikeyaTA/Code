import json
import os
import time  # <--- Added for tracking execution duration
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

# 🛠️ NEW: OpenTelemetry & Azure Monitor Imports
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import metrics

# Automatically boot up the Azure Monitor distribution pipeline.
# This automatically picks up the "APPLICATIONINSIGHTS_CONNECTION_STRING" 
# environment variable injected by Azure Container Apps.
configure_azure_monitor()

# 📊 NEW: Define Your Custom Dashboard Instruments
meter = metrics.get_meter("foundry_chat_backend")

# Metric 1: A counter to track total chat interactions and failure states
chat_turn_counter = meter.create_counter(
    name="chat_turns_total",
    description="Total inbound user chat messages processed by the gateway",
    unit="1"
)

# Metric 2: A histogram to track how long the AI Foundry Agent takes to compute a response
agent_latency_histogram = meter.create_histogram(
    name="agent_compute_duration_seconds",
    description="Time spent waiting for openai_client.responses.create to finish",
    unit="s"
)

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
PROJECT_ENDPOINT = "https://foundry-services-applications11-dev.services.ai.azure.com/api/projects/foundry-project-applications11-dev"
AGENT_ID = "Agent"
SESSIONS_DIR = Path(__file__).parent / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Foundry Chat Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ca-chat-frontend-dev.delightfulground-33da19a5.eastus.azurecontainerapps.io"
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

credential = DefaultAzureCredential()
project_client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
agent = project_client.agents.get(agent_name=AGENT_ID)
openai_client = project_client.get_openai_client()

# ... (Keep your original Pydantic models & session storage helper functions exactly the same) ...

# --------------------------------------------------------------------------
# Instrumented Routes
# --------------------------------------------------------------------------

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

    # ⏱️ START TIMER: Measure the precise AI Foundry processing threshold
    start_time = time.time()
    
    try:
        # 2. Call the agent response (handles execution automatically)
        response = openai_client.responses.create(
            conversation=conversation_id,
            extra_body={"agent_reference": {"name": agent.name, "type": "agent_reference"}},
        )
        
        # ⏱️ STOP TIMER
        execution_duration = time.time() - start_time

        if response.status == "failed":
            # 📊 RECORD METRIC: Track failed turn transitions
            chat_turn_counter.add(1, {"status": "failed", "error_code": "agent_failure"})
            raise HTTPException(status_code=502, detail=f"Agent response failed: {response.last_error}")

        # 📊 RECORD METRIC: Capture successful runs and latency distributions
        agent_latency_histogram.record(execution_duration, {"agent_id": AGENT_ID})
        chat_turn_counter.add(1, {"status": "success", "error_code": "none"})

    except Exception as e:
        # Catch unexpected infrastructure crashes (timeouts, network disconnects)
        chat_turn_counter.add(1, {"status": "exception", "error_code": type(e).__name__})
        raise e

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