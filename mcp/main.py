import os
import base64
from typing import Any

import httpx
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse
from dotenv import load_dotenv


# =============================================================================
# ENVIRONMENT & SERVICENOW CONFIGURATION
# =============================================================================

load_dotenv()

SERVICENOW_INSTANCE_URL = os.getenv(
    "SERVICENOW_INSTANCE_URL",
    "https://dev408306.service-now.com"
)

SERVICENOW_BASE_URL = f"{SERVICENOW_INSTANCE_URL}/api/now"

# ServiceNow PDI credentials (used strictly for backend calls to ServiceNow)
SERVICENOW_USERNAME = os.getenv("SERVICENOW_USERNAME", "admin")
SERVICENOW_PASSWORD = os.getenv("SERVICENOW_PASSWORD", "c5wfjC5C@!ZX")


# =============================================================================
# MCP SERVER
# =============================================================================

mcp = FastMCP("servicenow-mcp")


# =============================================================================
# SERVICENOW HELPERS
# =============================================================================

def _basic_auth_header() -> str:
    """
    Creates the HTTP Basic Authentication header for ServiceNow outbound calls.
    """
    credentials = f"{SERVICENOW_USERNAME}:{SERVICENOW_PASSWORD}"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    return f"Basic {encoded}"


def _get_servicenow_username() -> str:
    return SERVICENOW_USERNAME


async def get_servicenow_headers() -> dict[str, str]:
    return {
        "Authorization": _basic_auth_header(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def get_cached_username(request: Request | None = None) -> str:
    return _get_servicenow_username()


# =============================================================================
# SERVICENOW HTTP HELPERS
# =============================================================================

async def snow_get(
    path: str,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    headers = await get_servicenow_headers()

    async with httpx.AsyncClient(timeout=30) as client:
        return await client.get(
            f"{SERVICENOW_BASE_URL}{path}",
            params=params,
            headers=headers,
        )


async def snow_post(
    path: str,
    payload: dict[str, Any],
) -> httpx.Response:
    headers = await get_servicenow_headers()

    async with httpx.AsyncClient(timeout=30) as client:
        return await client.post(
            f"{SERVICENOW_BASE_URL}{path}",
            json=payload,
            headers=headers,
        )


async def snow_patch(
    path: str,
    payload: dict[str, Any],
) -> httpx.Response:
    headers = await get_servicenow_headers()

    async with httpx.AsyncClient(timeout=30) as client:
        return await client.patch(
            f"{SERVICENOW_BASE_URL}{path}",
            json=payload,
            headers=headers,
        )


def _display(data: dict[str, Any], field: str) -> str:
    val = data.get(field, {})

    return (
        val.get("display_value", "")
        if isinstance(val, dict)
        else str(val or "")
    )


# =============================================================================
# MCP TOOLS: INCIDENT MANAGEMENT
# =============================================================================

@mcp.tool()
async def create_incident(
    short_description: str,
    description: str = ""
) -> dict:
    """
    Create a new incident in ServiceNow.
    """
    print("Inside create_incident")

    state = "1"
    agent_identity_string = "Created by Roadie Ranger Agent - "

    payload = {
        "short_description": agent_identity_string + short_description,
        "state": state,
    }

    optional_fields = {"description": description}
    payload.update({k: v for k, v in optional_fields.items() if v})

    print("payload:", payload)

    response = await snow_post("/table/incident", payload)

    print("response:", response.status_code)

    if response.status_code not in (200, 201):
        return {
            "error": f"ServiceNow returned {response.status_code}",
            "detail": response.text,
        }

    body = response.json()
    data = body.get("result", {})

    if not isinstance(data, dict):
        return {
            "error": "Unexpected response from ServiceNow",
            "detail": str(body),
        }

    return {
        "sys_id": data.get("sys_id", ""),
        "number": data.get("number", ""),
        "short_description": data.get("short_description", ""),
        "state": _display(data, "state"),
        "created_on": data.get("sys_created_on", ""),
    }


@mcp.tool()
async def list_my_incidents() -> list:
    """
    Retrieve incidents opened by the configured ServiceNow user within the last 7 days.
    """
    print("Inside list_my_incidents")

    target_username = await get_cached_username()
    print(f"Target Username: {target_username}")

    query = (
        f"opened_by.user_name={target_username}"
        f"^sys_created_on>=javascript:gs.daysAgoStart(7)"
    )

    params = {
        "sysparm_query": query,
        "sysparm_display_value": "true",
        "sysparm_limit": 100,
    }

    response = await snow_get("/table/incident", params=params)

    print("response:", response.status_code)

    if response.status_code != 200:
        return [
            {
                "error": f"ServiceNow returned status code {response.status_code}",
                "detail": response.text,
            }
        ]

    data = response.json()
    return data.get("result", [])


@mcp.tool()
async def get_incident_by_number(ticket_number: str) -> dict:
    """
    Retrieve a ServiceNow incident by incident number.
    """
    print(f"Inside get_incident_by_number: {ticket_number}")

    target_username = await get_cached_username()
    print(f"Target Username: {target_username}")

    query = (
        f"number={ticket_number}"
        f"^opened_by.user_name={target_username}"
    )

    params = {
        "sysparm_query": query,
        "sysparm_display_value": "true",
        "sysparm_limit": 1,
    }

    response = await snow_get("/table/incident", params=params)

    if response.status_code != 200:
        return {
            "error": f"ServiceNow returned status code {response.status_code}",
            "detail": response.text,
        }

    data = response.json()
    results = data.get("result", [])

    if not results:
        return {
            "message": f"No ticket found matching number '{ticket_number}' for user '{target_username}'."
        }

    return results[0]


@mcp.tool()
async def update_incident(
    sys_id: str = "",
    ticket_id: str = "",
    comments: str = ""
) -> dict:
    """
    Update an existing ServiceNow incident.
    """
    print("Inside update_incident")

    if not sys_id and not ticket_id:
        return {"error": "Either sys_id or ticket_id must be provided."}

    print("sys_id:", sys_id)
    print("ticket_id:", ticket_id)

    if not sys_id and ticket_id:
        print("sys_id is absent and ticket_id is present")

        lookup_response = await snow_get(
            "/table/incident",
            params={
                "sysparm_query": f"number={ticket_id}",
                "sysparm_limit": "1",
                "sysparm_fields": "sys_id,number",
            },
        )

        if lookup_response.status_code != 200:
            return {
                "error": f"Failed to lookup incident {ticket_id}",
                "detail": lookup_response.text,
            }

        lookup_body = lookup_response.json()
        results = lookup_body.get("result", [])

        if not results:
            return {"error": f"Incident {ticket_id} not found"}

        sys_id = results[0]["sys_id"]

    fields = {"comments": comments}
    payload = {k: v for k, v in fields.items() if v}

    print("payload:", payload)

    if not payload:
        return {"message": "No update fields were provided. Record remains unchanged."}

    response = await snow_patch(f"/table/incident/{sys_id}", payload)

    print("response:", response.status_code)

    if response.status_code != 200:
        return {
            "error": f"ServiceNow returned status code {response.status_code}",
            "detail": response.text,
        }

    body = response.json()
    data = body.get("result", {})

    return {
        "sys_id": data.get("sys_id", ""),
        "number": data.get("number", ""),
        "short_description": data.get("short_description", ""),
        "updated_on": data.get("sys_updated_on", ""),
    }


# =============================================================================
# MCP TOOLS: KNOWLEDGE MANAGEMENT
# =============================================================================

async def search_kb_via_table_api(
    user_query: str,
    max_results: int = 2,
) -> list:
    print("Inside search_kb_via_table_api")

    url = f"{SERVICENOW_BASE_URL}/table/kb_knowledge"

    encoded_query = (
        f"IR_AND_OR_QUERY={user_query}"
        "^workflow_state=published"
        "^active=true"
    )

    params = {
        "sysparm_fields": "sys_id,number,short_description,text",
        "sysparm_query": encoded_query,
        "sysparm_limit": max_results,
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": _basic_auth_header(),
    }

    print("KB URL:", url)
    print("KB query:", encoded_query)

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=params, headers=headers)

    print("KB response status:", response.status_code)

    if response.status_code == 200:
        results = response.json().get("result", [])
        print(f"Retrieved {len(results)} KB articles")
        return results

    return [
        {
            "error": f"Search Error: {response.status_code}",
            "detail": response.text,
        }
    ]


@mcp.tool()
async def search_knowledge_articles(query: str) -> list[dict]:
    """
    Search the ServiceNow kb_knowledge table for relevant published and active articles.
    """
    print("Inside search_knowledge_articles")

    try:
        results = await search_kb_via_table_api(user_query=query, max_results=2)

        if not results:
            return []

        articles = []
        for item in results:
            if "error" in item:
                return [item]

            articles.append(
                {
                    "sys_id": item.get("sys_id", ""),
                    "number": item.get("number", ""),
                    "short_description": item.get("short_description", ""),
                    "text": item.get("text", ""),
                }
            )

        return articles

    except httpx.HTTPError as e:
        print(f"ServiceNow KB search failed: {e}")
        return [{"error": "ServiceNow KB search failed", "detail": str(e)}]

    except Exception as e:
        print(f"Unexpected KB search error: {e}")
        return [{"error": "Unexpected KB search error", "detail": str(e)}]


# =============================================================================
# HEALTH & ROUTE DEFINITIONS
# =============================================================================

@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    return JSONResponse(
        {
            "status": "ok",
            "service": "servicenow-mcp",
            "auth_mode": "unauthenticated",
        }
    )


@mcp.custom_route("/", methods=["GET"])
async def root_ping(request: Request):
    return JSONResponse(
        {
            "status": "running",
            "mcp_endpoint": "/mcp",
            "auth_mode": "unauthenticated",
        }
    )


# =============================================================================
# STARLETTE APP (UNAUTHENTICATED)
# =============================================================================

app = mcp.http_app(
    transport="http",
    path="/mcp",
    host_origin_protection=False,
)