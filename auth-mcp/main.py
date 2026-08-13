import os
import time
import json
import base64
import hashlib
import asyncio
from typing import Any
 
import httpx
import msal
 
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
 
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.datastructures import Headers
 
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from dotenv import load_dotenv

 
 
load_dotenv()
KEY_VAULT_NAME = os.getenv('KEY_VAULT_NAME')
KV_URI = f"https://{KEY_VAULT_NAME}.vault.azure.net"
print("Key Vaul URI",KV_URI)
 
key_vault_credential = DefaultAzureCredential()
key_vault_client = SecretClient(vault_url=KV_URI, credential=key_vault_credential)
 
SERVICENOW_INSTANCE_URL = os.getenv("SERVICENOW_INSTANCE_URL").rstrip("/")

 
SERVICENOW_BASE_URL = f"{SERVICENOW_INSTANCE_URL}/api/now"
print("SERVICENOW_BASE_URL", SERVICENOW_BASE_URL)
 
 
 
MCP_SERVER_CLIENT_ID = key_vault_client.get_secret("txrh-RoadieRangerDev-6279-StoSup-pHmO-snow-mcp-client-id").value
MCP_TENANT_ID = key_vault_client.get_secret("txrh-RoadieRangerDev-6279-StoSup-pHmO-snow-mcp-tenant-id").value
 
print("MCP_SERVER_CLIENT_ID", MCP_SERVER_CLIENT_ID)
print("MCP_TENANT_ID", MCP_TENANT_ID)
 
#####################################################################################################################
MCP_SERVER_CLIENT_SECRET = key_vault_client.get_secret("txrh-RoadieRangerDev-6279-StoSup-pHmO-snow-mcp-client-secret").value
print("MCP_SERVER_CLIENT_SECRET", MCP_SERVER_CLIENT_SECRET[:5])
#####################################################################################################################
 
TRUST_APP_CLIENT_ID = key_vault_client.get_secret("txrh-RoadieRangerDev-6279-StoSup-pHmO-trust-client-id").value
print("TRUST_APP_CLIENT_ID", TRUST_APP_CLIENT_ID)
 
 
# This is the Entra Trust App that ServiceNow validates as the token audience.
# TRUST_APP_CLIENT_ID = os.getenv("TRUST_APP_CLIENT_ID", "")
 
# If your Trust App App ID URI is api://<trust-app-client-id>.
# If you configured a custom App ID URI, replace this with that exact URI.
TRUST_APP_SCOPE = f"api://{TRUST_APP_CLIENT_ID}/user_impersonation"
print("TRUST_APP_SCOPE", TRUST_APP_SCOPE)
 
AUTHORITY = f"https://login.microsoftonline.com/{MCP_TENANT_ID}"
print("AUTHORITY", AUTHORITY)
 
# Cache buffer. If a token expires in less than this many seconds, refresh it.
TOKEN_EXPIRY_BUFFER_SECONDS = 300
 
mcp = FastMCP("servicenow-mcp")
 
 
# =============================================================================
# IN-MEMORY TOKEN CACHE
# =============================================================================
 
# Shape:
# {
#   "<cache-key>": {
#       "access_token": "...",
#       "expires_at": 1234567890
#   }
# }
 
_snow_token_cache: dict[str, dict[str, Any]] = {}
 
 
# =============================================================================
# AUTH MIDDLEWARE
# =============================================================================
 
class MCPAuthMiddleware:
    """
    Only verifies that a Bearer token exists.
 
    The OBO exchange will fail if the token is invalid,
    expired, or not intended for the MCP Server App.
    """
 
    def __init__(self, app):
        self.app = app
 
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
 
        if scope["path"] == "/health":
            await self.app(scope, receive, send)
            return
 
        headers = Headers(scope=scope)
 
        auth_header = headers.get("authorization", "")
 
        if not auth_header.lower().startswith("bearer "):
            response = JSONResponse(
                {"error": "Missing bearer token"},
                status_code=401
            )
            await response(scope, receive, send)
            return
 
        await self.app(scope, receive, send)
 
# =============================================================================
# TOKEN HELPERS
# =============================================================================
 
def _require_config() -> None:
    """
    Fail fast if required identity configuration is missing.
    """
    missing = []
 
    if not MCP_TENANT_ID:
        missing.append("MCP_TENANT_ID")
    if not MCP_SERVER_CLIENT_ID:
        missing.append("MCP_SERVER_CLIENT_ID")
    if not MCP_SERVER_CLIENT_SECRET:
        missing.append("MCP_SERVER_CLIENT_SECRET")
    if not TRUST_APP_CLIENT_ID:
        missing.append("TRUST_APP_CLIENT_ID")
    if not TRUST_APP_SCOPE:
        missing.append("TRUST_APP_SCOPE")
 
    if missing:
        raise RuntimeError(f"Missing required configuration: {', '.join(missing)}")
 
 
def _get_bearer_token_from_request(request: Request) -> str:
    """
    Reads the incoming token sent to the MCP server.
 
    In the target design, this token should be:
    aud = MCP Server App
    scope = access_as_user
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise PermissionError("Missing Authorization bearer token")
 
    return auth_header.split(" ", 1)[1].strip()
 
 
def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """
    Decodes JWT payload without verifying signature.
 
    This is only used for cache-key and expiry convenience.
    """
    try:
        print("inside _decode_jwt_payload")
        parts = token.split(".")
        if len(parts) < 2:
            return {}
 
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
        print("_decode_jwt_payload", json.loads(decoded.decode("utf-8")))
        return json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}
 
 
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
 
 
def _get_cache_key_from_user_assertion(user_assertion: str) -> str:
    """
    Prefer tid + oid as the cache key so the ServiceNow token is cached per user.
    Falls back to a token hash if claims are unavailable.
    """
    claims = _decode_jwt_payload(user_assertion)
    print("claims", claims)
 
    tid = claims.get("tid", "")
    oid = claims.get("oid", "") or claims.get("sub", "")
 
    if tid and oid:
        return f"{tid}:{oid}"
 
    return f"token_hash:{_hash_token(user_assertion)}"
 
 
def _is_cached_token_valid(cache_key: str) -> bool:
    item = _snow_token_cache.get(cache_key)
    if not item:
        return False
 
    expires_at = int(item.get("expires_at", 0))
    return expires_at > int(time.time()) + TOKEN_EXPIRY_BUFFER_SECONDS
 
 
def _build_msal_confidential_client() -> msal.ConfidentialClientApplication:
    _require_config()
 
    return msal.ConfidentialClientApplication(
        client_id=MCP_SERVER_CLIENT_ID,
        client_credential=MCP_SERVER_CLIENT_SECRET,
        authority=AUTHORITY,
    )
 
 
def _acquire_snow_token_obo_sync(user_assertion: str) -> dict[str, Any]:
    """
    Performs the actual OBO exchange:
 
    incoming token:
        aud = MCP Server App
 
    requested token:
        aud = ServiceNow Trust App
        scope = user_impersonation
    """
    app = _build_msal_confidential_client()
 
    result = app.acquire_token_on_behalf_of(
        user_assertion=user_assertion,
        scopes=[TRUST_APP_SCOPE],
    )
 
    if "access_token" not in result:
        raise RuntimeError(
            "OBO token acquisition failed: "
            f"{result.get('error')} - {result.get('error_description')}"
        )
 
    return result
 
 
async def get_servicenow_access_token(request: Request) -> str:
    """
    Main helper used by every ServiceNow tool.
 
    1. Read incoming MCP bearer token.
    2. Check in-memory cache.
    3. If cache miss or near expiry, perform OBO.
    4. Return ServiceNow Trust App token.
    """
    user_assertion = _get_bearer_token_from_request(request)
    print("user_assertion", user_assertion)
    cache_key = _get_cache_key_from_user_assertion(user_assertion)
    print("cache_key", cache_key)
 
    if _is_cached_token_valid(cache_key):
        print("Token is valid")
        return _snow_token_cache[cache_key]["access_token"]
 
    print("Creating new token")
    # MSAL's acquire_token_on_behalf_of is synchronous.
    # Run it in a thread so we do not block the async event loop.
    result = await asyncio.to_thread(_acquire_snow_token_obo_sync, user_assertion)
    print("result", result)
 
    now = int(time.time())
    expires_in = int(result.get("expires_in", 3600))
    expires_at = now + expires_in
 
    _snow_token_cache[cache_key] = {
        "access_token": result["access_token"],
        "expires_at": expires_at,
    }
 
    return result["access_token"]
 
 
async def get_servicenow_headers(request: Request) -> dict[str, str]:
    """
    Creates headers for ServiceNow calls using the OBO-acquired token.
    """
    snow_token = await get_servicenow_access_token(request)
 
    return {
        "Authorization": f"Bearer {snow_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
 
 
# =============================================================================
# SERVICENOW HTTP HELPERS
# =============================================================================
 
async def snow_get(
    request: Request,
    path: str,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    headers = await get_servicenow_headers(request)
    print("headers", headers)
 
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.get(
            f"{SERVICENOW_BASE_URL}{path}",
            params=params,
            headers=headers,
        )
 
 
async def snow_post(
    request: Request,
    path: str,
    payload: dict[str, Any],
) -> httpx.Response:
    headers = await get_servicenow_headers(request)
    print("headers", headers)
 
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.post(
            f"{SERVICENOW_BASE_URL}{path}",
            json=payload,
            headers=headers,
        )
 
 
async def snow_patch(
    request: Request,
    path: str,
    payload: dict[str, Any],
) -> httpx.Response:
    headers = await get_servicenow_headers(request)
    print("headers", headers)
 
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.patch(
            f"{SERVICENOW_BASE_URL}{path}",
            json=payload,
            headers=headers,
        )
 
 
def _display(data: dict[str, Any], field: str) -> str:
    val = data.get(field, {})
    return val.get("display_value", "") if isinstance(val, dict) else str(val or "")
 
 
# =============================================================================
# MCP TOOLS: INCIDENT MANAGEMENT
# =============================================================================
 
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
    """
    Create a new incident in ServiceNow.
 
    Auth behavior:
    - MCP receives the caller's bearer token.
    - MCP performs OBO to get a ServiceNow Trust App token.
    - ServiceNow evaluates the bearer token and applies its own user mapping,
      roles, and ACLs.
    """
    print("Inside the create_incident function")
    request = get_http_request()
 
    payload = {
        "short_description": short_description,
        "state": state,
    }
 
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
    print("payload", payload)
 
    response = await snow_post(request, "/table/incident", payload)
    print("response", response)
 
    if response.status_code not in (200, 201):
        return {
            "error": f"ServiceNow returned {response.status_code}",
            "detail": response.text,
        }
 
    body = response.json()
    data = body.get("result", {})
 
    if not isinstance(data, dict):
        return {"error": "Unexpected response from ServiceNow", "detail": str(body)}
 
    return {
        "sys_id": data.get("sys_id", ""),
        "number": data.get("number", ""),
        "short_description": data.get("short_description", ""),
        "state": _display(data, "state"),
        "priority": _display(data, "priority"),
        "created_on": data.get("sys_created_on", ""),
    }
 
 
@mcp.tool()
async def get_incidents_by_user(target_username: str) -> list:
    """
    Retrieve all incidents opened by a specific user within the last 7 days.
    """
    print("Inside the get_incidents_by_user function")
    request = get_http_request()
 
    query = f"opened_by.user_name={target_username}^sys_created_on>=javascript:gs.daysAgoStart(7)"
 
    params = {
        "sysparm_query": query,
        "sysparm_display_value": "true",
        "sysparm_limit": 100,
    }
 
    response = await snow_get(request, "/table/incident", params=params)
    print("response", response)
 
    if response.status_code != 200:
        return [{
            "error": f"ServiceNow returned status code {response.status_code}",
            "detail": response.text,
        }]
 
    data = response.json()
    return data.get("result", [])
 
 
@mcp.tool()
async def get_incident_by_number_and_user(
    ticket_number: str,
    target_username: str,
) -> dict:
    """
    Retrieve details of a specific incident using its ticket number and the
    username of the person who opened it.
    """
    request = get_http_request()
 
    query = f"number={ticket_number}^opened_by.user_name={target_username}"
 
    params = {
        "sysparm_query": query,
        "sysparm_display_value": "true",
        "sysparm_limit": 1,
    }
 
    response = await snow_get(request, "/table/incident", params=params)
 
    if response.status_code != 200:
        return {
            "error": f"ServiceNow returned status code {response.status_code}",
            "detail": response.text,
        }
 
    data = response.json()
    results = data.get("result", [])
 
    if not results:
        return {
            "message": (
                f"No ticket found matching number '{ticket_number}' "
                f"for user '{target_username}'."
            )
        }
 
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
    """
    Update an existing incident in ServiceNow using its sys_id.
    """
    request = get_http_request()
 
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
 
    response = await snow_patch(request, f"/table/incident/{sys_id}", payload)
 
    if response.status_code != 200:
        return {
            "error": f"ServiceNow returned status code {response.status_code}",
            "detail": response.text,
        }
 
    body = response.json()
    data = body.get("result", {})
 
    if not isinstance(data, dict):
        return {"error": "Unexpected response from ServiceNow", "detail": str(body)}
 
    return {
        "sys_id": data.get("sys_id", ""),
        "number": data.get("number", ""),
        "short_description": data.get("short_description", ""),
        "state": _display(data, "state"),
        "priority": _display(data, "priority"),
        "updated_on": data.get("sys_updated_on", ""),
    }
 
 
 
 
# =============================================================================
# MCP TOOLS: KNOWLEDGE MANAGEMENT
# =============================================================================
 
 
 
 
async def _search_knowledge_articles_v2(
    request: Request,
    query: str,
    limit: int = 5,
) -> list[dict]:
    """
    Helper method: Searches ServiceNow KM v2 API for relevant articles.
    """
    print("inside _search_knowledge_articles_v2 function")
    headers = await get_servicenow_headers(request)
    print("headers", headers)
 
    url = f"{SERVICENOW_INSTANCE_URL}/api/sn_km_api/knowledge/articles"
 
    params = {
        "text": query,
        "limit": limit,
        "fields": "sys_id,number,short_description",
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
 
    print("I am printing the articles")
    print(articles)
    return articles
 
 
async def _get_article_content_v2(
    request: Request,
    article_id: str,
) -> dict | None:
    """
    Helper method: Fetches full content for a single article ID using KM v2 API.
    """
    print("inside _get_article_content_v2 function")
    headers = await get_servicenow_headers(request)
    print("headers", headers)
 
    url = f"{SERVICENOW_INSTANCE_URL}/api/sn_km_api/knowledge/articles/{article_id}"
    print("url", url)
 
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
 
        if resp.status_code == 404:
            return None
 
        resp.raise_for_status()
        result = resp.json().get("result", {})
 
    content = result.get("content")
 
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
async def get_relevant_knowledge_articles(
    query: str,
    top_n: int = 3,
) -> list[dict]:
    """
    Search the ServiceNow knowledge base for articles relevant to a user's
    helpdesk question, then return the full content of the top matches.
    """
    request = get_http_request()
 
    try:
        candidates = await _search_knowledge_articles_v2(
            request=request,
            query=query,
            limit=top_n,
        )
        print("Retrieved the candidates")
    except httpx.HTTPError as e:
        print(f"ServiceNow search failed: {e}")
        return []
 
    articles = []
 
    for candidate in candidates:
        article_id = candidate.get("sys_id")
        if not article_id:
            continue
 
        try:
            full = await _get_article_content_v2(request, article_id)
            if full:
                articles.append(full)
        except httpx.HTTPError as e:
            print(f"Failed to fetch article {article_id}: {e}")
 
    return articles
 
 
# =============================================================================
# HEALTH ROUTE
# =============================================================================
 
async def health(request: Request):
    return JSONResponse({
        "status": "ok",
        "service": "servicenow-mcp",
        "auth_mode": "entra-obo-to-servicenow-trust-app",
    })
 
 
# =============================================================================
# STARLETTE APP
# =============================================================================
 
# app = mcp.http_app(transport="sse")
# app.add_middleware(MCPAuthMiddleware)
# app.add_route("/health", health, methods=["GET"])
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/", methods=["GET"])
async def root_ping(request: Request):
    return JSONResponse({"status": "running", "mcp_endpoint": "/mcp"})
 
app = mcp.http_app(
    transport="http",
    path="/snow",
    host_origin_protection=False
)
app.add_middleware(MCPAuthMiddleware)
 
