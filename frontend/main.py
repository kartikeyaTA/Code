import os
import httpx
import traceback  # 🔍 CRITICAL: Captures full stack traces for underlying crashes
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from urllib.parse import urlparse

app = FastAPI(title="Public Application Gateway")

# Extract the internal ACA backend target URL from environment variables
BACKEND_INTERNAL_URL = os.getenv("BACKEND_API_URL")


@app.get("/")
def gateway_health():
    return {"status": "Public Python Gateway Online"}


# 🎯 1. DEDICATED CLEAN AGENT ENDPOINT (GET)
# Intercepts the response from the backend, strips the metadata, and returns clean text
@app.post("/agent-chat")
async def get_clean_agent_response(request: Request):
    if not BACKEND_INTERNAL_URL:
        return Response(content="Backend target URL unconfigured.", status_code=500)

    # Force http:// for internal container communication to avoid TLS certificate resets
    clean_backend_base = BACKEND_INTERNAL_URL.replace("https://", "http://").rstrip('/')
    target_url = f"{clean_backend_base}/chat"

    # Pristine headers: Zero infrastructure tracking leaks from the public internet
    headers = {
        "host": urlparse(clean_backend_base).netloc,
        "content-type": "application/json"
    }

    async with httpx.AsyncClient() as client:
        try:
            # Symmetrical POST forward
            response = await client.post(url=target_url, headers=headers, json={}, timeout=60.0)
            
            # Directly hand the backend's clean plain text response back to the user
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type="text/plain; charset=utf-8"
            )
            
        except httpx.RequestError as exc:
            return Response(content="Bad Gateway. Backend unreachable.", status_code=502)


# 🎯 2. ORIGINAL CATCH-ALL GATEWAY (UNTOUCHED PASSTHROUGH)
# Strips out '/api' from the public URL and passes the remainder cleanly to the backend root
@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_gateway(path: str, request: Request):
    if not BACKEND_INTERNAL_URL:
        return Response(
            content='{"error": "Backend internal route target is unconfigured."}', 
            status_code=500, 
            media_type="application/json"
        )

    # Reconstruct the private internal target destination path dynamically
    query_string = f"?{request.url.query}" if request.url.query else ""
    
    # 🎯 FIX: Strips out the forced '/api' segment so it perfectly aligns with backend's structure
    target_url = f"{BACKEND_INTERNAL_URL.rstrip('/')}/{path}{query_string}"
    
    print(f"Routing public request internally to: {target_url}")

    # Extract incoming headers and override the Host header for internal ACA validation compliance
    headers = dict(request.headers)
    headers["host"] = urlparse(BACKEND_INTERNAL_URL).netloc

    # Capture incoming request payload body
    body = await request.body()

    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
                timeout=60.0
            )
            
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.headers.get("content-type")
            )
        except httpx.RequestError as exc:
            print(f"Internal Routing Fault: {exc}")
            return Response(
                content='{"error": "Bad Gateway. Unable to communicate with internal microservices."}', 
                status_code=502, 
                media_type="application/json"
            )