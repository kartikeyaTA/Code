import os
import base64
import httpx
from fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.datastructures import Headers

# --- CONFIGURATION ---
SERVICENOW_BASE_URL = "https://dev408306.service-now.com/api/now"
SERVICENOW_USERNAME = "admin"
SERVICENOW_PASSWORD = "c5wfjC5C@!ZX"
MCP_API_KEY = "mcp_key"
MCP_API_KEY_HEADER = "x-api-key"

mcp = FastMCP("servicenow-mcp")


# --- MIDDLEWARE ---
class APIKeyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # We only care about HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Bypass authentication for the health endpoint
        if scope["path"] == "/health":
            await self.app(scope, receive, send)
            return

        # Extract headers safely using Starlette's Headers wrapper
        headers = Headers(scope=scope)
        incoming_key = headers.get(MCP_API_KEY_HEADER, "")

        if incoming_key != MCP_API_KEY:
            response = JSONResponse({"error": "Unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        # Hand over control to the next layer/application
        await self.app(scope, receive, send)


# --- HELPERS ---
def _basic_auth_header() -> str:
    token = base64.b64encode(f"{SERVICENOW_USERNAME}:{SERVICENOW_PASSWORD}".encode()).decode()
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
    """Create a new incident in ServiceNow.

    Args:
        short_description: Brief description of the incident (required, max 160 chars).
        description: Detailed description of the incident.
        caller_id: Username or sys_id of the person reporting the incident.
        category: Category — one of: network, hardware, software, database, inquiry, other.
        subcategory: Subcategory of the incident.
        impact: Impact level — 1=High, 2=Medium, 3=Low.
        urgency: Urgency level — 1=High, 2=Medium, 3=Low.
        priority: Priority — 1=Critical, 2=High, 3=Moderate, 4=Low, 5=Planning.
        assignment_group: Name or sys_id of the group to assign the incident to.
        assigned_to: Username or sys_id of the person assigned.
        state: State — 1=New, 2=In Progress, 3=On Hold, 4=Resolved, 5=Closed, 6=Canceled.
        contact_type: How reported — email, phone, self-service, walk-in, monitoring.
        location: Location associated with the incident.
        cmdb_ci: Configuration Item related to the incident.
        work_notes: Internal notes (not visible to caller).
        comments: Additional comments (visible to caller).
    """
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
        return {"error": "Unexpected response from ServiceNow", "detail": str(body)}

    def display(field):
        val = data.get(field, {})
        return val.get("display_value", "") if isinstance(val, dict) else str(val)

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
    """Retrieve all incidents opened by a specific user within the last 7 days.

    Args:
        target_username: The system username (user_name) of the person who opened the incidents.
    """
    url = f"{SERVICENOW_BASE_URL}/table/incident"
    query = f"opened_by.user_name={target_username}^sys_created_on>=javascript:gs.daysAgoStart(7)"

    params = {
        "sysparm_query": query,
        "sysparm_display_value": "true",
        "sysparm_limit": 100
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": _basic_auth_header()
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data.get("result", [])


@mcp.tool()
async def search_kb_via_table_api(user_query: str, max_results: int = 5) -> list:
    """Queries the ServiceNow kb_knowledge table using a raw string text search.
    Returns a list of matching published active articles. Good for RAG contexts.

    Args:
        user_query: The keywords or query string to search for within the knowledge base.
        max_results: Maximum number of articles to return (defaults to 5).
    """
    url = f"{SERVICENOW_BASE_URL}/table/kb_knowledge"
    encoded_query = f"IR_AND_OR_QUERY={user_query}^workflow_state=published^active=true"

    params = {
        "sysparm_fields": "sys_id,number,short_description,text",
        "sysparm_query": encoded_query,
        "sysparm_limit": max_results
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": _basic_auth_header()
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=params, headers=headers)

        if response.status_code == 200:
            return response.json().get('result', [])
        else:
            return [{"error": f"Search Error: {response.status_code}", "detail": response.text}]


@mcp.tool()
async def get_incident_by_number_and_user(ticket_number: str, target_username: str) -> dict:
    """Retrieve details of a specific incident using its ticket number and the username of the person who opened it.

    Args:
        ticket_number: The exact incident number (e.g., 'INC0010001').
        target_username: The system username (user_name) of the person who opened the ticket.
    """
    url = f"{SERVICENOW_BASE_URL}/table/incident"
    query = f"number={ticket_number}^opened_by.user_name={target_username}"

    params = {
        "sysparm_query": query,
        "sysparm_display_value": "true",
        "sysparm_limit": 1  # We only expect a single exact match
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": _basic_auth_header()
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=params, headers=headers)

        if response.status_code != 200:
            return {
                "error": f"ServiceNow returned status code {response.status_code}",
                "detail": response.text
            }

        data = response.json()
        results = data.get("result", [])

        if not results:
            return {"message": f"No ticket found matching number '{ticket_number}' for user '{target_username}'."}

        # Returns the full JSON object of the single matching ticket
        return results[0]

# --- STARLETTE APPS / ROUTES ---
async def health(request: Request):
    return JSONResponse({"status": "ok"})


app = mcp.http_app(transport="sse")
app.add_middleware(APIKeyMiddleware)
app.add_route("/health", health, methods=["GET"])