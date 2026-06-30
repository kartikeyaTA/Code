import os
import httpx
from fastapi import FastAPI, Query, HTTPException

app = FastAPI(title="Azure Core Service")

SECRET_VAL = os.getenv("MY_DUMMY_SECRET", "Secret not injected yet")
STORAGE_ACCOUNT_NAME = os.getenv("STORAGE_ACCOUNT_NAME", "")
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")

# 🤖 APIM Internal Target Configurations
APIM_INTERNAL_IP = os.getenv("APIM_INTERNAL_IP", "10.0.2.4")
APIM_SUBSCRIPTION_KEY = os.getenv("APIM_SUBSCRIPTION_KEY", "28ef3a364e3d4e239b900473b0857653")
APIM_HOST_HEADER = os.getenv("APIM_HOST_HEADER", "apim-gateway-chat3-dev.azure-api.net")

@app.get("/")
def read_root():
    return {
        "Status": "Online",
        "InjectedSecret": SECRET_VAL
    }

@app.post("/chat")
async def chat_with_agent():
    """
    Executes the exact verified static curl payload internally within the VNet.
    """
    url = f"http://{APIM_INTERNAL_IP}/agent/openai/v1/responses"
    
    headers = {
        "Host": APIM_HOST_HEADER,
        "Content-Type": "application/json",
        "api-key": APIM_SUBSCRIPTION_KEY
    }
    
    # 🎯 Exact JSON body from your working curl command
    payload = {
        "model": "o4-mini",
        "input": [
            {
                "role": "user",
                "content": "Tell me what you can help with."
            }
        ]
    }
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code, 
                    detail=f"APIM Backend Error: {response.text}"
                )
            
            # Return the raw JSON block provided by the agent engine directly
            return response.json()
            
    except httpx.RequestError as exc:
        raise HTTPException(status_code=500, detail=f"Internal network connection failure: {str(exc)}")

@app.get("/list-blobs")
def list_blobs(container: str = Query(None)):
    if not STORAGE_ACCOUNT_NAME:
        return {"ERROR":"STORAGE_ACCOUNT_NAME environment variable is missing."}
    
    try:
        if AZURE_CLIENT_ID:
            credential = DefaultAzureCredential(managed_identity_client_id=AZURE_CLIENT_ID)
        else:
            credential = DefaultAzureCredential()
            
        account_url = f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net"
        blob_service_client = BlobServiceClient(account_url, credential=credential)
        
        if container:
            container_client = blob_service_client.get_container_client(container)
            blobs_list = [blob.name for blob in container_client.list_blobs()]
            return {
                "StorageAccount": STORAGE_ACCOUNT_NAME,
                "Container": container,
                "Files": blobs_list
            }
        
        containers = blob_service_client.list_containers()
        return {
            "StorageAccount": STORAGE_ACCOUNT_NAME,
            "ContainersFound": [c.name for c in containers]
        }
    except Exception as e:
        return {"error": str(e)}