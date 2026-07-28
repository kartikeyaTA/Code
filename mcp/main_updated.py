import os
import json
import base64
import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse

# --------------------------------------------------------------------------
# CONFIGURATION
# --------------------------------------------------------------------------
SERVICENOW_BASE_URL = "https://dev408306.service-now.com/api/now"

# Proves the caller is Foundry (static, rotated manually via Key Vault).
MCP_CALLER_SECRET = os.environ["MCP_CALLER_SECRET"]
MCP_CALLER_SECRET_HEADER = "x-mcp-caller-secret"

# Used for the client_id/azp verification step below.
# EXPECTED_SERVICENOW_CLIENT_ID = os.environ["SERVICENOW_OAUTH_CLIENT_ID"]  # the Foundry-Agent-Passthrough client ID

mcp = FastMCP("servicenow-mcp")


# --------------------------------------------------------------------------
# MIDDLEWARE — caller-secret check only. This is NOT the user's identity
# check; it just proves "this request came from Foundry, not a random caller
# on the internet who found this endpoint."
# --------------------------------------------------------------------------
class CallerSecretMiddleware:
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
        incoming_secret = headers.get(MCP_CALLER_SECRET_HEADER, "")

        if incoming_secret != MCP_CALLER_SECRET:
            response = JSONResponse({"error": "Unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


# --------------------------------------------------------------------------
# HELPERS
# --------------------------------------------------------------------------
def _get_forwarded_user_token(request: Request) -> str:
    """
    Pulls the ServiceNow user token that Foundry's OAuth Identity Passthrough
    attached to this call. This is Priya's own token — every tool call reads
    it fresh from the request, never from a module-level constant.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise ValueError("Missing forwarded ServiceNow bearer token")
    return auth_header.split(" ", 1)[1].strip()


# def _verify_client_id_if_possible(token: str) -> None:
#     """
#     Best-effort check that the forwarded token was actually minted through the
#     Foundry-Agent-Passthrough OAuth app, by inspecting a client_id/azp claim.

#     CAVEAT — confirm before relying on this: ServiceNow's default OAuth access
#     tokens (issued from oauth_token.do) are typically opaque bearer strings,
#     NOT decodable JWTs. If that's the case for your instance, this function
#     will legitimately find nothing to decode, and this check degrades to a
#     no-op. In that case the client_id/azp guarantee has to come from
#     ServiceNow's own token introspection endpoint instead (RFC 7662-style,
#     if enabled on your instance) rather than local decoding — verify which
#     situation you're in against your actual dev408306 instance before
#     treating this as a real security boundary. Don't assume JWT.
#     """
#     parts = token.split(".")
#     if len(parts) != 3:
#         # Not a JWT shape — can't verify locally. Log and continue; the
#         # caller-secret check above remains the enforced gate in this case.
#         return

#     try:
#         padded = parts[1] + "=" * (-len(parts[1]) % 4)
#         payload = json.loads(base64.urlsafe_b64decode(padded))
#     except Exception:
#         return

#     claim_client_id = payload.get("client_id") or payload.get("azp")
#     if claim_client_id and claim_client_id != EXPECTED_SERVICENOW_CLIENT_ID:
#         raise ValueError("Forwarded token was not issued to the expected OAuth client")


# --------------------------------------------------------------------------
# MCP TOOLS
# --------------------------------------------------------------------------
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
    """Create a new incident in ServiceNow, acting as the calling user.

    Args:
        short_description: Brief description of the incident (required, max 160 chars).
        description: Detailed description of the incident.
        caller_id: Username or sys_id of the person reporting the incident.
            NOTE: this is informational only — it is NOT what determines who
            the ticket is actually created as. Actual identity/authorization
            comes from the forwarded ServiceNow OAuth token (see below), not
            from this field. Do not treat this argument as a trust boundary.
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
    request = get_http_request()

    try:
        user_token = _get_forwarded_user_token(request)
        # _verify_client_id_if_possible(user_token)
    except ValueError as e:
        return {"error": "Unauthorized", "detail": str(e)}

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
                "Authorization": f"Bearer {user_token}",  # Priya's token, forwarded as-is
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


# --------------------------------------------------------------------------
# STARLETTE APPS / ROUTES
# --------------------------------------------------------------------------
async def health(request: Request):
    return JSONResponse({"status": "ok"})


app = mcp.http_app(transport="sse")
app.add_middleware(CallerSecretMiddleware)
app.add_route("/health", health, methods=["GET"])
