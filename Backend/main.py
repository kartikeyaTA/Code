import os
import httpx
from fastapi import FastAPI, Query, HTTPException,Request, Response
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

app = FastAPI(title="Azure Core Service")

SECRET_VAL = os.getenv("AgentVersion", "Secret not injected yet")
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
FOUNDRY_ENDPOINT = os.getenv(
    "FOUNDRY_ENDPOINT",
    "https://foundry-services-applications4-dev.services.ai.azure.com/api/projects/foundry-project-applications4-dev/openai/v1/responses"
)

# APIM Configuration
# APIM_INTERNAL_IP = os.getenv("APIM_INTERNAL_IP", "10.0.2.4")
# APIM_SUBSCRIPTION_KEY = os.getenv("APIM_SUBSCRIPTION_KEY", "28ef3a364e3d4e239b900473b0857653")
# APIM_HOST_HEADER = os.getenv("APIM_HOST_HEADER", "apim-gateway-chat3-dev.azure-api.net")

AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")

if AZURE_CLIENT_ID:
    print(f"Production Mode: Initializing Managed Identity with Client ID: {AZURE_CLIENT_ID}")
    # Explicitly binds to your User-Assigned Identity inside the ACA container
    credential = DefaultAzureCredential(managed_identity_client_id=AZURE_CLIENT_ID)
else:
    print("Development Mode: Falling back to local developer Azure CLI credentials...")
    # Automatically scans your local machine for an active 'az login' session
    credential = DefaultAzureCredential()
    
@app.get("/")
def read_root():
    return {
        "Status": "Online",
        "InjectedSecret": SECRET_VAL
    }

@app.post("/chat")
async def chat_with_agent(request: Request):
    """
    Secure proxy endpoint with a hardcoded payload.
    Extracts clean text answer at the source to prevent upstream transport header corruption.
    """
    try:
        # 🔑 DYNAMIC AUTHENTICATION: Fetch a fresh token for the AI Foundry data-plane audience
        token_struct = credential.get_token("https://ai.azure.com/.default")
        bearer_token = token_struct.token
        
        # 🔒 HARDCODED PAYLOAD: The precise payload used in your successful Agent Version 2 test
        hardcoded_payload = {
            "input": [
                {
                    "role": "user",
                    "content": "Tell me what you can help with, and confirm your active runtime context."
                }
            ],
            "agent_reference": {
                "name": "Agent",
                "version": SECRET_VAL,
                "type": "agent_reference"
            }
        }

        print("Forwarding hardcoded test payload to Azure AI Foundry...")

        # Securely forward the payload down into the internal network endpoint
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url=FOUNDRY_ENDPOINT,
                json=hardcoded_payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {bearer_token}"
                }
            )
            
            print(f"Received response from Foundry. HTTP Status: {response.status_code}")
            
            # 🎯 NEW STRATEGY: Extract and clean the text right here at the source!
            if response.status_code == 200:
                try:
                    response_json = response.json()
                    clean_text = None
                    
                    # Safely loop through output payload blocks to extract the plain markdown response
                    for block in response_json.get("output", []):
                        if block.get("type") == "message" and "content" in block:
                            content_array = block["content"]
                            if content_array and isinstance(content_array, list):
                                clean_text = content_array[0].get("text")
                                break
                    
                    if clean_text:
                        print("🎯 Successfully isolated clean agent markdown text string. Returning to frontend.")
                        # 🎯 FIX: Return JUST the text content, completely eliminating raw downstream headers!
                        return Response(
                            content=clean_text,
                            status_code=200,
                            media_type="text/plain; charset=utf-8"
                        )
                except Exception as parse_err:
                    print(f"Backend failed to parse inner text from JSON structure: {parse_err}")

            # Safe Fallback: If AI call failed or parsing missed, return raw content 
            # but CRUCIALY do not copy response.headers which poisons Envoy.
            print("Fallback triggered. Returning unparsed content context.")
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type="application/json"
            )

    except httpx.RequestError as net_err:
        print(f"Network Connection Failure to Foundry: {net_err}")
        return Response(
            content='{"error": "Bad Gateway. AI Foundry target unreachable."}',
            status_code=502,
            media_type="application/json"
        )
    except Exception as exc:
        print(f"Internal Runtime Exception: {str(exc)}")
        return Response(
            content=f'{{"error": "Internal Server Error: {str(exc)}"}}',
            status_code=500,
            media_type="application/json"
        )
        
# @app.post("/chat")
# async def chat_with_agent():
#     """
#     Executes the exact verified static HTTPS curl payload internally within the VNet.
#     Uses custom transport routing to map port 443 to the internal APIM IP.
#     """
#     # 🎯 The target URL must use the clean HTTPS domain path
#     url = f"https://{APIM_HOST_HEADER}/agent/openai/v1/responses"
    
#     headers = {
#         "Content-Type": "application/json",
#         "api-key": APIM_SUBSCRIPTION_KEY
#     }
    
#     payload = {
#         "model": "o4-mini",
#         "input": [
#             {
#                 "role": "user",
#                 "content": "Tell me what you can help with."
#             }
#         ]
#     }
    
#     # 🗺️ Bypasses standard DNS by manually forcing the hostname map straight to the internal IP
#     # This mirrors the '--resolve apim-gateway-chat3-dev.azure-api.net:443:10.0.2.4' flag perfectly.
#     local_dns_mapping = {
#         f"{APIM_HOST_HEADER}:443": (APIM_INTERNAL_IP, 443)
#     }
    
#     # 🔓 Set verify=False to mirror the '--insecure' flag for internal self-signed TLS routing
#     transport = httpx.AsyncHTTPTransport(
#         local_address=None, 
#         uds=None, 
#         proxy=None, 
#         verify=False
#     )
    
#     # Inject the resolution rules directly into the mounting dictionary
#     transport._pool._local_address = None
    
#     try:
#         # Build the client context executing over the custom internal connection block
#         async with httpx.AsyncClient(transport=transport, timeout=60.0) as client:
#             # We use an internal override hack since httpx doesn't natively expose an elegant '--resolve' macro API
#             # Creating an explicit custom connection transport map for socket binding:
            
#             # Reconstruct target address with explicit connection pooling rules
#             response = await client.post(
#                 f"https://{APIM_INTERNAL_IP}/agent/openai/v1/responses", 
#                 json=payload, 
#                 headers={**headers, "Host": APIM_HOST_HEADER}
#             )
            
#             if response.status_code != 200:
#                 raise HTTPException(
#                     status_code=response.status_code, 
#                     detail=f"APIM Backend Error: {response.text}"
#                 )
            
#             return response.json()
            
#     except httpx.RequestError as exc:
#         raise HTTPException(status_code=500, detail=f"Internal network connection failure: {str(exc)}")
    


# @app.get("/list-blobs")
# def list_blobs(container: str = Query(None)):
#     if not STORAGE_ACCOUNT_NAME:
#         return {"ERROR":"STORAGE_ACCOUNT_NAME environment variable is missing."}
#     else:
#         print(f"Targeting Storage Account: {STORAGE_ACCOUNT_NAME}")
    
#     try:
#         # ◄ FIXED: Pass the Client ID explicitly so the SDK targets your User-Assigned Identity
#         if AZURE_CLIENT_ID:
#             credential = DefaultAzureCredential(managed_identity_client_id=AZURE_CLIENT_ID)
#         else:
#             credential = DefaultAzureCredential()
            
#         account_url = f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net"
#         blob_service_client = BlobServiceClient(account_url, credential=credential)
        
#         # SCENARIO A: User requested files inside a specific container
#         if container:
#             container_client = blob_service_client.get_container_client(container)
#             blobs_list = [blob.name for blob in container_client.list_blobs()]
            
#             return {
#                 "StorageAccount": STORAGE_ACCOUNT_NAME,
#                 "Container": container,
#                 "Files": blobs_list
#             }
        
#         # SCENARIO B: Default fallback (No container specified) -> List all containers
#         containers = blob_service_client.list_containers()
#         container_list = [c.name for c in containers]
        
#         return {
#             "StorageAccount": STORAGE_ACCOUNT_NAME,
#             "ContainersFound": container_list
#         }
        
#     except Exception as e:
#         return {"error": str(e)}