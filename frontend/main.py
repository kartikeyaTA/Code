import os
import httpx
from fastapi import FastAPI, Request, Response
from urllib.parse import urlparse

app = FastAPI()

# Extract the internal ACA backend target URL from environment variables
BACKEND_INTERNAL_URL = os.getenv("BACKEND_API_URL")

@app.get("/")
def health_check():
    return {"status": "Public Python Gateway Online"}

# Catch-all proxy route to capture all HTTP verbs and sub-paths securely
@app.api_route("/api/", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_gateway(path: str, request: Request):
    if not BACKEND_INTERNAL_URL:
        return Response(
            content='{"error": "Backend internal route target is unconfigured."}', 
            status_code=500, 
            media_type="application/json"
        )

    # Reconstruct the private internal target destination path
    query_string = f"?{request.url.query}" if request.url.query else ""
    target_url = f"{BACKEND_INTERNAL_URL}"
    
    print(f"Routing public request internally to: {target_url}")

    # Extract incoming headers and override the Host header for internal ACA validation compliance
    headers = dict(request.headers)
    headers["host"] = urlparse(BACKEND_INTERNAL_URL).netloc

    # Capture incoming request payload body
    body = await request.body()

    async with httpx.AsyncClient() as client:
        try:
            # Asynchronously proxy the entire payload to the backend service
            response = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
                timeout=60.0
            )
            
            # Forward the exact response back to the client browser
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