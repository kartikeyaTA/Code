import os
import httpx
from fastapi import FastAPI, Query, HTTPException,Request, Response
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
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
        
@app.post("/admin/register-mcp")
async def bootstrap_mcp_connection():
    if not FOUNDRY_ENDPOINT:
        raise HTTPException(status_code=400, detail="FOUNDRY_ENDPOINT environment variable is missing.")
        
    try:
        print("Connecting to Azure AI Foundry via backend infrastructure identity...")
        project_client = AIProjectClient.from_connection_string(
            conn_str=FOUNDRY_ENDPOINT,
            credential=credential
        )
        
        print("Registering the internal VNet-isolated MCP container application...")
        # This will securely register your private backend container tool mapping
        connection = project_client.connections.create_or_update(
            connection_name="Private-MCP-Backend-Connection",
            connection_type="Custom",
            endpoint="https://mcp-backend-dev.politeglacier-2e13f3f5.eastus2.azurecontainerapps.io/sse",
            credentials={"key": "My-Foundry-Secure-Key-2026"},  # Your key-based token string
            metadata={"apiType": "MCP"}
        )
        
        return {"status": "Success", "message": "Private MCP tool registered seamlessly inside the VNet!"}
        
    except Exception as e:
        print(f"Failed to bootstrap MCP connection: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")