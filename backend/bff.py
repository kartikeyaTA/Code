"""
BFF (Backend-For-Frontend) Auth & Proxy Service
Run: uvicorn bff:app --reload --port 8000
"""

import os
import uuid
import httpx
import msal

from fastapi import FastAPI, Request, HTTPException, Cookie, Depends
from fastapi.responses import JSONResponse, RedirectResponse

app = FastAPI(title="BFF Auth & Proxy Service")

# ---------------------------------------------------------------------------
# Config (set these as environment variables)
# ---------------------------------------------------------------------------
CLIENT_ID = os.environ.get("AAD_CLIENT_ID", "your-client-id")
CLIENT_SECRET = os.environ.get("AAD_CLIENT_SECRET", "your-client-secret")
TENANT_ID = os.environ.get("AAD_TENANT_ID", "your-tenant-id")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"

REDIRECT_URI = os.environ.get("AAD_REDIRECT_URI", "http://localhost:8000/auth/callback")
LOGIN_SCOPES = os.environ.get("AAD_LOGIN_SCOPES", "User.Read").split()
FOUNDRY_SCOPES = os.environ.get("FOUNDRY_SCOPES", "api://your-foundry-scope/.default").split()
BACKEND_SCOPES = os.environ.get("BACKEND_SCOPES", "api://your-backend-scope/.default").split()
POST_LOGIN_REDIRECT = os.environ.get("POST_LOGIN_REDIRECT", "http://localhost:3000/")

# Where your FastAPI Backend is running
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8001")

# ---------------------------------------------------------------------------
# In-memory storage (Use Redis for production)
# ---------------------------------------------------------------------------
pending_logins: dict[str, dict] = {}   # flow_id -> MSAL flow dict
sessions: dict[str, dict] = {}         # session_id -> user & tokens


def msal_app() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=CLIENT_ID, client_credential=CLIENT_SECRET, authority=AUTHORITY
    )


# ---------------------------------------------------------------------------
# Authentication Endpoints
# ---------------------------------------------------------------------------
@app.get("/login")
def login():
    flow = msal_app().initiate_auth_code_flow(scopes=LOGIN_SCOPES, redirect_uri=REDIRECT_URI)
    if "auth_uri" not in flow:
        return JSONResponse(status_code=401, content={"error": "Failed to build auth URL"})

    flow_id = str(uuid.uuid4())
    pending_logins[flow_id] = flow

    response = RedirectResponse(flow["auth_uri"])
    response.set_cookie("login_flow_id", flow_id, httponly=True, samesite="lax", max_age=600)
    return response


@app.get("/auth/callback")
def auth_callback(request: Request):
    flow_id = request.cookies.get("login_flow_id")
    flow = pending_logins.pop(flow_id, None) if flow_id else None
    
    if not flow:
        return JSONResponse(status_code=401, content={"error": "No matching login in progress"})

    try:
        result = msal_app().acquire_token_by_auth_code_flow(flow, dict(request.query_params))
    except ValueError as e:
        return JSONResponse(status_code=401, content={"error": str(e)})

    if "error" in result:
        return JSONResponse(status_code=401, content={"error": result.get("error_description")})

    user_token = result["access_token"]
    username = result.get("id_token_claims", {}).get("preferred_username", "unknown")

    # OBO token exchanges
    foundry_result = msal_app().acquire_token_on_behalf_of(user_assertion=user_token, scopes=FOUNDRY_SCOPES)
    if "error" in foundry_result:
        return JSONResponse(status_code=401, content={"error": foundry_result.get("error_description")})

    backend_result = msal_app().acquire_token_on_behalf_of(user_assertion=user_token, scopes=BACKEND_SCOPES)
    if "error" in backend_result:
        return JSONResponse(status_code=401, content={"error": backend_result.get("error_description")})

    # Save session
    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        "user": username,
        "foundry_token": foundry_result["access_token"],
        "backend_token": backend_result["access_token"],
    }

    response = RedirectResponse(POST_LOGIN_REDIRECT)
    response.set_cookie("session_id", session_id, httponly=True, secure=True, samesite="lax", max_age=600)
    return response


# ---------------------------------------------------------------------------
# Proxy Endpoints
# ---------------------------------------------------------------------------
def get_current_session(session_id: str = Cookie(None)):
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Session invalid or expired.")
    return sessions[session_id]


@app.post("/api/conversations")
async def proxy_create_conversation(session: dict = Depends(get_current_session)):
    backend_token = session["backend_token"]
    foundry_token = session["foundry_token"]
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BACKEND_URL}/conversations",
            headers={
                "Authorization": f"Bearer {backend_token}",
                "X-Foundry-Token": foundry_token
            },
            timeout=10.0
        )
        
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
        
    return response.json()


@app.post("/api/chat")
async def proxy_chat(request: Request, session: dict = Depends(get_current_session)):
    backend_token = session["backend_token"]
    foundry_token = session["foundry_token"]
    
    body = await request.json()
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BACKEND_URL}/chat",
            headers={
                "Authorization": f"Bearer {backend_token}",
                "X-Foundry-Token": foundry_token
            },
            json=body,
            timeout=60.0
        )
        
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
        
    return response.json()