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
SERVICENOW_INSTANCE_URL = "https://dev408306.service-now.com"
SERVICENOW_BASE_URL = f"{SERVICENOW_INSTANCE_URL}/api/now"
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


# --- MCP TOOLS (INCIDENT MANAGEMENT) ---

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
    """Retrieve all incidents opened by a specific user within the last 7 days."""
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
async def get_incident_by_number_and_user(ticket_number: str, target_username: str) -> dict:
    """Retrieve details of a specific incident using its ticket number and the username of the person who opened it."""
    url = f"{SERVICENOW_BASE_URL}/table/incident"
    query = f"number={ticket_number}^opened_by.user_name={target_username}"

    params = {
        "sysparm_query": query,
        "sysparm_display_value": "true",
        "sysparm_limit": 1 
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
    """Update an existing incident in ServiceNow using its sys_id."""
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

@mcp.tool()
async def close_incident_by_number(
    ticket_number: str,
    close_code: str = "Solved (Permanently)",
    close_notes: str = "Ticket closed automatically by Customer Support Agent.",
) -> dict:
    """Closes an existing incident in ServiceNow using its ticket number."""
    headers = {
        "Authorization": _basic_auth_header(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        search_url = f"{SERVICENOW_BASE_URL}/table/incident"
        search_params = {
            "sysparm_query": f"number={ticket_number}",
            "sysparm_fields": "sys_id",
            "sysparm_limit": 1
        }

        search_response = await client.get(search_url, params=search_params, headers=headers)

        if search_response.status_code != 200:
            return {
                "error": f"Failed to look up ticket number. ServiceNow returned status {search_response.status_code}",
                "detail": search_response.text
            }

        search_results = search_response.json().get("result", [])
        if not search_results:
            return {"error": f"No ticket found with ticket number '{ticket_number}'."}

        sys_id = search_results[0].get("sys_id")

        update_url = f"{SERVICENOW_BASE_URL}/table/incident/{sys_id}"
        payload = {
            "state": "5",
            "close_code": close_code,
            "close_notes": close_notes
        }

        response = await client.patch(update_url, json=payload, headers=headers)

    if response.status_code != 200:
        return {
            "error": f"ServiceNow rejected closure with status code {response.status_code}",
            "detail": response.text,
        }

    body = response.json()
    data = body.get("result", {})

    return {
        "ticket_number": data.get("number", ticket_number),
        "sys_id": data.get("sys_id", ""),
        "status": "Closed",
        "message": f"Ticket {ticket_number} has been successfully closed."
    }


# --- MCP TOOLS (KNOWLEDGE MANAGEMENT) ---

@mcp.tool()
async def search_kb_via_table_api(user_query: str, max_results: int = 2) -> list:
    """Queries the ServiceNow kb_knowledge table using a raw string text search.
    Returns a list of matching published active articles. Good for RAG contexts."""
    print(f"🔍 Searching ServiceNow KB for query: '{user_query}' (max {max_results} results)...")
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
    print(f"🔗 GET {url}?{params} with headers {headers}")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=params, headers=headers)
        print(f"📦 ServiceNow KB search response status: {response.status_code}")
        if response.status_code == 200:
            return response.json().get('result', [])
        else:
            return [{"error": f"Search Error: {response.status_code}", "detail": response.text}]


async def _search_knowledge_articles_v2(query: str, limit: int = 5) -> list[dict]:
    """Helper method: Searches KM v2 API for relevant articles."""
    url = f"{SERVICENOW_INSTANCE_URL}/api/sn_km_api/knowledge/articles"
    params = {
        "text": query,
        "limit": limit,
        "fields": "sys_id,number,short_description",
    }
    headers = {
        "Accept": "application/json",
        "Authorization": _basic_auth_header()
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        body = resp.json()

    results = body.get("result", {}).get("results", []) or body.get("result", [])
    articles = []
    for item in results:
        articles.append({
            "sys_id": item.get("sys_id") or item.get("id"),
            "number": item.get("number"),
            "title": item.get("short_description") or item.get("title"),
            "snippet": item.get("snippet", ""),
        })
    return articles


async def _get_article_content_v2(article_id: str) -> dict | None:
    """Helper method: Fetches full content for a single article ID using KM v2 API."""
    url = f"{SERVICENOW_INSTANCE_URL}/api/sn_km_api/knowledge/articles/{article_id}"
    headers = {
        "Accept": "application/json",
        "Authorization": _basic_auth_header()
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        result = resp.json().get("result", {})

    content = result.get("content")
    
    # Templated articles return content as a list of {label, value} fields
    # instead of a single string — flatten it if so.
    if isinstance(content, list):
        content = "\n\n".join(
            f"{field_.get('label', '')}: {field_.get('value', '')}"
            for field_ in content
        )

    return {
        "sys_id": result.get("sys_id"),
        "number": result.get("number"),
        "title": result.get("short_description") or result.get("title"),
        "content": content or "",
        "url": f"{SERVICENOW_INSTANCE_URL}/kb_view.do?sys_kb_id={result.get('sys_id')}",
    }


@mcp.tool()
async def get_relevant_knowledge_articles(query: str, top_n: int = 3) -> list[dict]:
    """
    Search the ServiceNow knowledge base for articles relevant to a user's helpdesk 
    question, and return the full content of the top matches so the agent can ground 
    its answer in them.
    
    Args:
        query: The user's question or issue description.
        top_n: Number of articles to retrieve (keep low — 2-3 is usually enough).
    """
    try:
        candidates = await _search_knowledge_articles_v2(query, limit=top_n)
    except httpx.HTTPError as e:
        print(f"ServiceNow search failed: {e}")
        return []

    articles = []
    for c in candidates:
        try:
            full = await _get_article_content_v2(c["sys_id"])
            if full:
                articles.append(full)
        except httpx.HTTPError as e:
            print(f"Failed to fetch article {c['sys_id']}: {e}")

    return articles

# --- STARLETTE APPS / ROUTES ---
async def health(request: Request):
    return JSONResponse({"status": "ok"})


app = mcp.http_app(transport="sse")
app.add_middleware(APIKeyMiddleware)
app.add_route("/health", health, methods=["GET"])
