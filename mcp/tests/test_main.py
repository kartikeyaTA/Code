import pytest
import httpx
from unittest.mock import MagicMock, patch
from main_backend import (
    app,
    create_incident,
    get_incidents_by_user,
    search_kb_via_table_api,
    get_incident_by_number_and_user,
    update_incident,
    close_incident_by_number,
    _basic_auth_header,
)


# ==============================================================================
# 1. MIDDLEWARE & HTTP ENDPOINT TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_health_endpoint_bypasses_auth():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_middleware_rejects_missing_api_key():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/sse")
        assert response.status_code == 401
        assert response.json() == {"error": "Unauthorized"}


@pytest.mark.asyncio
async def test_middleware_rejects_invalid_api_key():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"x-api-key": "wrong_key"},
    ) as client:
        response = await client.get("/sse")
        assert response.status_code == 401


def test_basic_auth_header_format():
    header = _basic_auth_header()
    assert header.startswith("Basic ")


# ==============================================================================
# 2. MCP TOOL TESTS (Using MagicMock for httpx.Response)
# ==============================================================================

@pytest.mark.asyncio
@patch("httpx.AsyncClient.post")
async def test_create_incident_success(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {
        "result": {
            "sys_id": "sys123456",
            "number": "INC0010001",
            "short_description": "Server down",
            "state": "1",
            "priority": "1",
            "sys_created_on": "2026-08-03 10:00:00",
        }
    }
    mock_post.return_value = mock_resp

    result = await create_incident(short_description="Server down")
    assert result["sys_id"] == "sys123456"
    assert result["number"] == "INC0010001"


@pytest.mark.asyncio
@patch("httpx.AsyncClient.post")
async def test_create_incident_failure(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    mock_post.return_value = mock_resp

    result = await create_incident(short_description="Server down")
    assert "error" in result


@pytest.mark.asyncio
@patch("httpx.AsyncClient.get")
async def test_get_incidents_by_user_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = lambda: None
    mock_resp.json.return_value = {
        "result": [
            {"number": "INC0010001", "short_description": "VPN issue"},
            {"number": "INC0010002", "short_description": "Laptop broken"},
        ]
    }
    mock_get.return_value = mock_resp

    result = await get_incidents_by_user("admin")
    assert len(result) == 2


@pytest.mark.asyncio
@patch("httpx.AsyncClient.get")
async def test_search_kb_via_table_api_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "result": [
            {"sys_id": "kb001", "short_description": "How to reset password"}
        ]
    }
    mock_get.return_value = mock_resp

    articles = await search_kb_via_table_api("password reset")
    assert len(articles) == 1


@pytest.mark.asyncio
@patch("httpx.AsyncClient.get")
async def test_search_kb_via_table_api_failure(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "Not Found"
    mock_get.return_value = mock_resp

    articles = await search_kb_via_table_api("password reset")
    assert "error" in articles[0]


@pytest.mark.asyncio
@patch("httpx.AsyncClient.get")
async def test_get_incident_by_number_and_user_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": [{"number": "INC0010001"}]}
    mock_get.return_value = mock_resp

    res = await get_incident_by_number_and_user("INC0010001", "admin")
    assert res["number"] == "INC0010001"


@pytest.mark.asyncio
@patch("httpx.AsyncClient.patch")
async def test_update_incident_success(mock_patch):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "result": {
            "sys_id": "sys123",
            "number": "INC001",
            "short_description": "Updated Description",
        }
    }
    mock_patch.return_value = mock_resp

    res = await update_incident(sys_id="sys123", short_description="Updated Description")
    assert res["sys_id"] == "sys123"


@pytest.mark.asyncio
@patch("httpx.AsyncClient.patch")
@patch("httpx.AsyncClient.get")
async def test_close_incident_by_number_success(mock_get, mock_patch):
    mock_get_resp = MagicMock()
    mock_get_resp.status_code = 200
    mock_get_resp.json.return_value = {"result": [{"sys_id": "sys123"}]}
    mock_get.return_value = mock_get_resp

    mock_patch_resp = MagicMock()
    mock_patch_resp.status_code = 200
    mock_patch_resp.json.return_value = {
        "result": {"sys_id": "sys123", "number": "INC0010001"}
    }
    mock_patch.return_value = mock_patch_resp

    res = await close_incident_by_number("INC0010001")
    assert res["status"] == "Closed"