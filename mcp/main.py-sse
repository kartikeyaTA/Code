import base64
import contextvars
import os
import httpx
from fastmcp import FastMCP
from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

# --- CONTEXT VAR ---
# Safely holds the token per-request across concurrent executions
snow_token_var = contextvars.ContextVar("snow_token", default="")

# --- CONFIGURATION ---
SERVICENOW_BASE_URL = "https://dev408306.service-now.com/api/now"
SERVICENOW_USERNAME = "admin"
SERVICENOW_PASSWORD_FALLBACK = "c5wfjC5C@!ZX"

mcp = FastMCP("servicenow-mcp")


# --- MIDDLEWARE ---
class ServiceNowTokenMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # We only process HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract headers using Starlette's Headers wrapper
        headers = Headers(scope=scope)
        print("I am printing all the headers")
        print(headers)

        # Extract ServiceNow secret key / token passed from headers
        incoming_snow_key = headers.get("my-snow-secret-key", "")
        token_id = snow_token_var.set(incoming_snow_key)

        try:
            # Pass control to the application layer
            await self.app(scope, receive, send)
        finally:
            # Reset context var to prevent leakage across requests
            snow_token_var.reset(token_id)


# --- HELPERS ---
def _basic_auth_header() -> str:
    # Retrieve dynamic token from current request context
    snow_password = snow_token_var.get()

    # Fallback to default password if header was not provided
    if not snow_password:
        snow_password = SERVICENOW_PASSWORD_FALLBACK

    token = base64.b64encode(
        f"{SERVICENOW_USERNAME}:{snow_password}".encode()
    ).decode()
    return f"Basic {token}"


# --- MCP TOOLS ---
@mcp.tool()
async def create_incident(
    short_description: str,
    description: str = "",
    caller_id: str = "",
    category: str = "",
    subcategory: str = "",
    impact: str = "",
    urgency: str = "",
    priority: str = "",
    assignment_group: str = "",
    assigned_to: str = "",
    state: str = "1",
    contact_type: str = "",
    location: str = "",
    cmdb_ci: str = "",
    work_notes: str = "",
    comments: str = "",
) -> dict:
    """Create a new incident in ServiceNow."""
    payload = {"short_description": short_description, "state": state}

    optional_fields = {
        "description": description,
        "caller_id": caller_id,
        "category": category,
        "subcategory": subcategory,
        "impact": impact,
        "urgency": urgency,
        "priority": priority,
        "assignment_group": assignment_group,
        "assigned_to": assigned_to,
        "contact_type": contact_type,
        "location": location,
        "cmdb_ci": cmdb_ci,
        "work_notes": work_notes,
        "comments": comments,
    }
    payload.update({k: v for k, v in optional_fields.items() if v})

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{SERVICENOW_BASE_URL}/table/incident",
            json=payload,
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

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

    def display(field):
        val = data.get(field, {})
        return (
            val.get("display_value", "")
            if isinstance(val, dict)
            else str(val)
        )

    return {
        "sys_id": data.get("sys_id", ""),
        "number": data.get("number", ""),
        "short_description": data.get("short_description", ""),
        "state": display("state"),
        "priority": display("priority"),
        "created_on": data.get("sys_created_on", ""),
    }


@mcp.tool()
async def get_incidents_by_user(target_username: str) -> list:
    """Retrieve all incidents opened by a specific user within the last 7 days."""
    url = f"{SERVICENOW_BASE_URL}/table/incident"
    query = f"opened_by.user_name={target_username}^sys_created_on>=javascript:gs.daysAgoStart(7)"

    params = {
        "sysparm_query": query,
        "sysparm_display_value": "true",
        "sysparm_limit": 100,
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": _basic_auth_header(),
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data.get("result", [])


@mcp.tool()
async def search_kb_via_table_api(
    user_query: str, max_results: int = 2
) -> list:
    """Queries the ServiceNow kb_knowledge table using a raw string text search."""
    url = f"{SERVICENOW_BASE_URL}/table/kb_knowledge"
    encoded_query = (
        f"IR_AND_OR_QUERY={user_query}^workflow_state=published^active=true"
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

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=params, headers=headers)

        if response.status_code == 200:
            return response.json().get("result", [])
        else:
            return [
                {
                    "error": f"Search Error: {response.status_code}",
                    "detail": response.text,
                }
            ]


# --- STARLETTE APPS / ROUTES ---
async def health(request: Request):
    return JSONResponse({"status": "ok"})


app = mcp.http_app(transport="sse")
app.add_middleware(ServiceNowTokenMiddleware)
app.add_route("/health", health, methods=["GET"])