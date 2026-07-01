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
@app.get("/agent-chat")
async def get_clean_agent_response(request: Request):
    print("\n" + "="*50)
    print("🚀 [DEBUG] INCOMING PUBLIC REQUEST RECEIVED ON /api/agent-chat")
    print("="*50)
    
    try:
        # 1. Inspect Every Single Incoming Header From the Client/Envoy
        print(f"[DEBUG] Request Method: {request.method} | Source URL: {request.url}")
        print("[DEBUG] --- RAW INCOMING HEADERS FROM CLIENT ---")
        for key, value in request.headers.items():
            print(f"  -> {key}: {value}")
        print("[DEBUG] -----------------------------------------")

        if not BACKEND_INTERNAL_URL:
            print("[DEBUG] ❌ ERROR: BACKEND_API_URL environment variable is UNCONFIGURED!")
            return JSONResponse(
                status_code=500,
                content={"error": "Backend internal route target is unconfigured."}
            )

        target_url = f"{BACKEND_INTERNAL_URL.rstrip('/')}/chat"
        print(f"[DEBUG] Target Backend Destination Path: {target_url}")

        # 2. Header scrubbing tracing logic
        hop_by_hop = ["content-length", "host", "connection", "keep-alive", "transfer-encoding", "upgrade", "x-request-id"]
        k_low = k.lower()
        headers = {}
        print("[DEBUG] --- SCRUBBING HEADERS FOR INTERNAL COMPLIANCE ---")
        for k, v in request.headers.items():
            if k.lower() in hop_by_hop:
                print(f"  [EXCLUDED] Dropping hop-by-hop tracking key: '{k}'")
                continue
            if k.startswith(":"):
                print(f"  [EXCLUDED] Dropping HTTP/2 unique pseudo-header key: '{k}'")
                continue
            if k_low.startswith("x-envoy") or k_low.startswith("x-k8se") or k_low.startswith("x-ms") or k_low.startswith("x-arr") or k_low.startswith("x-forwarded"):
                print(f"  [EXCLUDED] Dropping internal Azure infrastructure token: '{k}'")
                continue
            headers[k] = v
        
        # Explicitly map required downstream proxy anchors
        headers["host"] = urlparse(BACKEND_INTERNAL_URL).netloc
        headers["content-type"] = "application/json"

        print("[DEBUG] --- FINAL SCRUBBED HEADERS SENT TO BACKEND ---")
        for k, v in headers.items():
            print(f"  -> {k}: {v}")
        print("[DEBUG] ----------------------------------------------")

        # 3. Execute the internal cross-container payload delivery
        print("[DEBUG] Dispatching HTTP POST transaction request to backend microservice...")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url=target_url,
                headers=headers,
                json={}, 
                timeout=60.0
            )
        
        # 4. Trace the results coming back inside the private vnet
        print(f"[DEBUG] ✅ BACKEND SERVICE HAS RESPONDED! HTTP Status: {response.status_code}")
        print("[DEBUG] --- RAW BACKEND RESPONSE HEADERS ---")
        for k, v in response.headers.items():
            print(f"  -> {k}: {v}")
        print("[DEBUG] -------------------------------------")

        content_type = response.headers.get("content-type", "")
        
        if response.status_code == 200 and "application/json" in content_type:
            print("[DEBUG] Target matched: Status 200 + JSON payload found. Starting token block processing parsing extraction...")
            try:
                response_json = response.json()
                clean_text = None
                
                for block in response_json.get("output", []):
                    if block.get("type") == "message" and "content" in block:
                        clean_text = block["content"][0].get("text")
                        print("[DEBUG] 🎯 Core Assistant plain-text answer block cleanly located.")
                        break
                
                if clean_text:
                    print("[DEBUG] Success! Delivering extracted plaintext content back to user client.")
                    return PlainTextResponse(content=clean_text, status_code=200)
                else:
                    print("[DEBUG] ⚠️ WARNING: Azure returned a valid structural JSON mapping, but no text block matched criteria.")
            except Exception as parse_err:
                print(f"[DEBUG] ❌ Exception during inner JSON content-matrix loop decomposition: {parse_err}")

        print("[DEBUG] Standard parsing skipped or target criteria unmet. Falling back to unparsed downstream data forwarding.")
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type=content_type if content_type else "application/json"
        )
            
    except httpx.RequestError as net_err:
        print(f"[DEBUG] ❌ Network Connection Failure to backend resource cluster link: {net_err}")
        return JSONResponse(status_code=502, content={"error": "Bad Gateway. Backend unreachable."})
        
    except Exception as general_err:
        # 🎯 THE CATCH-ALL SAFETY GATE: This records the explicit breakdown profile 
        print("\n" + "🚨 "*10)
        print("[DEBUG] CRITICAL PROCESS EXCEPTION BLOWUP DETECTED IN GATEWAY CORE PIPELINE!")
        print(f"[DEBUG] Exception Class Profile: {type(general_err).__name__}")
        print(f"[DEBUG] Primary Exception Context: {str(general_err)}")
        print("[DEBUG] --- DETAILED DUMP EXTENSION STACK TRACE ---")
        traceback.print_exc()  # Prints the exact file line number that cracked directly into stdout logs
        print("🚨 "*10 + "\n")
        
        return JSONResponse(
            status_code=500, 
            content={"error": f"Internal Gateway Structural Error: {str(general_err)}"}
        )


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