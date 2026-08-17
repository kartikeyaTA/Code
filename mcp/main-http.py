import base64
import contextvars
import os
import httpx
from fastmcp import FastMCP
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse

# --- CONTEXT VAR ---
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
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        incoming_snow_key = headers.get("my-snow-secret-key", "")
        token_id = snow_token_var.set(incoming_snow_key)

        try:
            await self.app(scope, receive, send)
        finally:
            snow_token_var.reset(token_id)


# --- HELPERS ---
def _basic_auth_header() -> str:
    snow_password = snow_token_var.get()
    if not snow_password:
        snow_password = SERVICENOW_PASSWORD_FALLBACK

    token = base64.b64encode(
        f"{SERVICENOW_USERNAME}:{snow_password}".encode()
    ).decode()
    return f"Basic {token}"


# --- HEALTH & ROOT ROUTES ---
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/", methods=["GET"])
async def root_ping(request: Request):
    return JSONResponse({"status": "running", "mcp_endpoint": "/mcp"})


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

        return results[0]


@mcp.tool()
async def update_incident(
    sys_id: str,
    short_description: str = "",
    description: str = "",
    state: str = "",
    impact: str = "",
    urgency: str = "",
    priority: str = "",
    assignment_group: str = "",
    assigned_to: str = "",
    work_notes: str = "",
    comments: str = "",
) -> dict:
    """Update an existing incident in ServiceNow using its sys_id.

    Args:
        sys_id: The unique 32-character identifier (sys_id) of the incident record.
        short_description: Updated brief summary of the incident.
        description: Updated detailed description.
        state: Update state — 1=New, 2=In Progress, 3=On Hold, 4=Resolved, 5=Closed, 6=Canceled.
        impact: Update impact level — 1=High, 2=Medium, 3=Low.
        urgency: Update urgency level — 1=High, 2=Medium, 3=Low.
        priority: Update priority — 1=Critical, 2=High, 3=Moderate, 4=Low, 5=Planning.
        assignment_group: Name or sys_id of the group to assign the incident to.
        assigned_to: Username or sys_id of the person assigned.
        work_notes: Internal notes to append (not visible to caller).
        comments: Customer-facing comments to append (visible to caller).
    """
    url = f"{SERVICENOW_BASE_URL}/table/incident/{sys_id}"

    fields = {
        "short_description": short_description,
        "description": description,
        "state": state,
        "impact": impact,
        "urgency": urgency,
        "priority": priority,
        "assignment_group": assignment_group,
        "assigned_to": assigned_to,
        "work_notes": work_notes,
        "comments": comments,
    }

    payload = {k: v for k, v in fields.items() if v}

    if not payload:
        return {"message": "No update fields were provided. Record remains unchanged."}

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.patch(
            url,
            json=payload,
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    if response.status_code != 200:
        return {
            "error": f"ServiceNow returned status code {response.status_code}",
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
        "updated_on": data.get("sys_updated_on", ""),
    }


# @mcp.tool()
# async def close_incident_by_number(
#     ticket_number: str,
#     close_code: str = "Solved (Permanently)",
#     close_notes: str = "Ticket closed automatically by Customer Support Agent.",
# ) -> dict:
#     """Closes an existing incident in ServiceNow using its ticket number.

#     Args:
#         ticket_number: The display number of the incident (e.g., 'INC0010001').
#         close_code: The reason the ticket is being closed. Standard OOTB options include:
#                     'Solved (Permanently)', 'Solved (Workaround)', 'Solved by Change',
#                     'Not Solved (Not Reproducible)', 'Not Solved (Too Costly)'.
#         close_notes: Summary details explaining the final resolution.
#     """
#     headers = {
#         "Authorization": _basic_auth_header(),
#         "Content-Type": "application/json",
#         "Accept": "application/json",
#     }

#     async with httpx.AsyncClient(timeout=30) as client:
#         # Step 1: Look up sys_id from ticket number
#         search_url = f"{SERVICENOW_BASE_URL}/table/incident"
#         search_params = {
#             "sysparm_query": f"number={ticket_number}",
#             "sysparm_fields": "sys_id",
#             "sysparm_limit": 1
#         }

#         search_response = await client.get(search_url, params=search_params, headers=headers)

#         if search_response.status_code != 200:
#             return {
#                 "error": f"Failed to look up ticket number. ServiceNow returned status {search_response.status_code}",
#                 "detail": search_response.text
#             }

#         search_results = search_response.json().get("result", [])
#         if not search_results:
#             return {"error": f"No ticket found with ticket number '{ticket_number}'."}

#         sys_id = search_results[0].get("sys_id")

#         # Step 2: Update state to Closed (state='5')
#         update_url = f"{SERVICENOW_BASE_URL}/table/incident/{sys_id}"
#         payload = {
#             "state": "5",
#             "close_code": close_code,
#             "close_notes": close_notes
#         }

#         response = await client.patch(update_url, json=payload, headers=headers)

#     if response.status_code != 200:
#         return {
#             "error": f"ServiceNow rejected closure with status code {response.status_code}",
#             "detail": response.text,
#         }

#     body = response.json()
#     data = body.get("result", {})

#     return {
#         "ticket_number": data.get("number", ticket_number),
#         "sys_id": data.get("sys_id", ""),
#         "status": "Closed",
#         "message": f"Ticket {ticket_number} has been successfully closed."
#     }


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
app = mcp.http_app(
    transport="http",
    path="/mcp",
    host_origin_protection=False
)

app.add_middleware(ServiceNowTokenMiddleware)
