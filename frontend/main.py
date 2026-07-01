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


# 🎯 1. DEDICATED CLEAN AGENT ENDPOINT (GET)
# Intercepts the response from the backend, strips the metadata, and returns clean text
@app.get("/agent-chat")
async def get_clean_agent_response(request: Request):
    # 🎯 GLOBAL SAFETY NET: Ensure the container NEVER drops the connection abruptly
    try:
        if not BACKEND_INTERNAL_URL:
            return JSONResponse(
                status_code=500,
                content={"error": "Backend internal route target is unconfigured."}
            )

        target_url = f"{BACKEND_INTERNAL_URL.rstrip('/')}/chat"
        print(f"Gateway converting GET request to internal Backend POST -> {target_url}")

        # 🎯 SCRUB PSEUDO-HEADERS: Strip hop-by-hop flags AND any HTTP/2 keys starting with ':'
        hop_by_hop = ["content-length", "host", "connection", "keep-alive", "transfer-encoding", "upgrade", "x-request-id"]
        
        headers = {}
        for k, v in request.headers.items():
            if k.lower() not in hop_by_hop and not k.startswith(":"):
                headers[k] = v
        
        # Inject standard compliant target headers
        headers["host"] = urlparse(BACKEND_INTERNAL_URL).netloc
        headers["content-type"] = "application/json"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url=target_url,
                headers=headers,
                json={}, 
                timeout=60.0
            )
            
            # If the backend returns a clean response, extract the text payload
            if response.status_code == 200 and "application/json" in response.headers.get("content-type", ""):
                response_json = response.json()
                for block in response_json.get("output", []):
                    if block.get("type") == "message" and "content" in block:
                        clean_text = block["content"][0].get("text")
                        if clean_text:
                            # 🎯 Native plain text response alignment
                            return PlainTextResponse(content=clean_text, status_code=200)

            # Safe Fallback: Pass backend content out without copying corrupt headers
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type=response.headers.get("content-type", "application/json")
            )
            
    except httpx.RequestError as net_err:
        print(f"Gateway network connection error: {net_err}")
        return JSONResponse(status_code=502, content={"error": "Bad Gateway. Backend unreachable."})
    except Exception as general_err:
        print(f"Critical Gateway Runtime Exception: {general_err}")
        return JSONResponse(status_code=500, content={"error": f"Internal Server Error: {str(general_err)}"})


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