import os
import httpx
from fastapi import FastAPI, Request, Response
from urllib.parse import urlparse

app = FastAPI(title="Public Application Gateway")

# Extract the internal ACA backend target URL from environment variables
BACKEND_INTERNAL_URL = os.getenv("BACKEND_API_URL")


@app.get("/")
def gateway_health():
    return {"status": "Public Python Gateway Online"}

# 🎯 CATCH-ALL GATEWAY: Strips the "/api" prefix and handles all microservice paths
@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_gateway(path: str, request: Request):
    if not BACKEND_INTERNAL_URL:
        return Response(
            content='{"error": "Backend internal route target is unconfigured."}', 
            status_code=500, 
            media_type="application/json"
        )

    # Reconstruct query strings if any exist
    query_string = f"?{request.url.query}" if request.url.query else ""
    
    # 🎯 FIX: Strips out '/api' and routes directly to the backend's root structure
    base_url = BACKEND_INTERNAL_URL.rstrip("/")
    target_url = f"{base_url}/{path}{query_string}"
    
    print(f"Proxying request: {request.method} {request.url.path} -> {target_url}")

    # Synchronize and clean headers for internal container environment routing compliance
    headers = dict(request.headers)
    headers["host"] = urlparse(BACKEND_INTERNAL_URL).netloc

    # Read incoming request body payload
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
            print(f"Gateway connection error: {exc}")
            return Response(
                content='{"error": "Bad Gateway. Backend microservice unreachable."}', 
                status_code=502, 
                media_type="application/json"
            )