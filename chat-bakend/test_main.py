import os
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------
# 1. Set Mock Environment Variables BEFORE importing app.py
# ---------------------------------------------------------------------
os.environ["PROJECT_ENDPOINT"] = "https://mock-endpoint.cognitiveservices.azure.com"
os.environ["AGENT_NAME"] = "mock-agent-name"

# ---------------------------------------------------------------------
# 2. Patch Azure SDK Clients BEFORE app import
# ---------------------------------------------------------------------
with patch("azure.identity.DefaultAzureCredential"), \
     patch("azure.ai.projects.AIProjectClient") as mock_ai_client_cls:

    # Setup mock Azure Project Client and OpenAI Client
    mock_client_instance = MagicMock()
    mock_ai_client_cls.return_value = mock_client_instance

    mock_agent = MagicMock()
    mock_agent.name = "mock-agent-name"
    mock_client_instance.agents.get.return_value = mock_agent

    mock_openai_client = MagicMock()
    mock_client_instance.get_openai_client.return_value = mock_openai_client

    # Import app module after patching global SDK calls
    from main import app, openai_client


@pytest.fixture
def client():
    """FastAPI TestClient Fixture."""
    return TestClient(app)


# ---------------------------------------------------------------------
# 3. Health Check Endpoint Tests (/health)
# ---------------------------------------------------------------------
def test_health_check_success(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------
# 4. Conversation Endpoint Tests (/conversations)
# ---------------------------------------------------------------------
def test_create_conversation_success(client):
    mock_conversation = MagicMock()
    mock_conversation.id = "conv_abc123456"
    openai_client.conversations.create.return_value = mock_conversation

    response = client.post("/conversations")

    assert response.status_code == 200
    assert response.json() == {"conversation_id": "conv_abc123456"}
    openai_client.conversations.create.assert_called_once()


def test_create_conversation_exception_failure(client):
    openai_client.conversations.create.side_effect = Exception("Azure Connection Refused")

    response = client.post("/conversations")

    assert response.status_code == 500
    assert "Failed to create conversation: Azure Connection Refused" in response.json()["detail"]


# ---------------------------------------------------------------------
# 5. Chat Endpoint Tests (/chat)
# ---------------------------------------------------------------------
def test_chat_success(client):
    mock_response = MagicMock()
    mock_response.status = "completed"
    mock_response.output_text = "Hello! How can I assist you with Azure today?"
    openai_client.responses.create.return_value = mock_response

    payload = {
        "conversation_id": "conv_abc123456",
        "user_query": "Hello AI Agent"
    }
    response = client.post("/chat", json=payload)

    assert response.status_code == 200
    assert response.json() == {
        "conversation_id": "conv_abc123456",
        "reply": "Hello! How can I assist you with Azure today?"
    }


def test_chat_agent_execution_failed_status(client):
    mock_response = MagicMock()
    mock_response.status = "failed"
    mock_response.last_error = "Agent model execution timeout"
    openai_client.responses.create.return_value = mock_response

    payload = {
        "conversation_id": "conv_abc123456",
        "user_query": "Run heavy workload"
    }
    response = client.post("/chat", json=payload)

    assert response.status_code == 502
    assert "Agent execution failed: Agent model execution timeout" in response.json()["detail"]


def test_chat_generic_exception(client):
    openai_client.responses.create.side_effect = Exception("Rate limit exceeded")

    payload = {
        "conversation_id": "conv_abc123456",
        "user_query": "Trigger error"
    }
    response = client.post("/chat", json=payload)

    assert response.status_code == 500
    assert "Rate limit exceeded" in response.json()["detail"]


def test_chat_invalid_payload_validation_error(client):
    # Missing required 'user_query' parameter
    payload = {"conversation_id": "conv_abc123456"}
    response = client.post("/chat", json=payload)

    assert response.status_code == 422
