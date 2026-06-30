import os
from fastapi import FastAPI, Query
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

app = FastAPI(title="Azure Core Service")

SECRET_VAL = os.getenv("MY_DUMMY_SECRET", "Secret not injected yet")

# 2. Storage Account Target (Passed as an Env Var)
STORAGE_ACCOUNT_NAME = os.getenv("STORAGE_ACCOUNT_NAME", "")

# 3. Fetch the Client ID of the User-Assigned Managed Identity injected by your Pipeline
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")

@app.get("/")
def read_root():
    return {
        "Status": "Online",
        "InjectedSecret": SECRET_VAL
    }

@app.get("/list-blobs")
def list_blobs(container: str = Query(None)):
    if not STORAGE_ACCOUNT_NAME:
        return {"error":"STORAGE_ACCOUNT_NAME environment variable is missing."}
    else:
        print(f"Targeting Storage Account: {STORAGE_ACCOUNT_NAME}")
    
    try:
        # ◄ FIXED: Pass the Client ID explicitly so the SDK targets your User-Assigned Identity
        if AZURE_CLIENT_ID:
            credential = DefaultAzureCredential(managed_identity_client_id=AZURE_CLIENT_ID)
        else:
            credential = DefaultAzureCredential()
            
        account_url = f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net"
        blob_service_client = BlobServiceClient(account_url, credential=credential)
        
        # SCENARIO A: User requested files inside a specific container
        if container:
            container_client = blob_service_client.get_container_client(container)
            blobs_list = [blob.name for blob in container_client.list_blobs()]
            
            return {
                "StorageAccount": STORAGE_ACCOUNT_NAME,
                "Container": container,
                "Files": blobs_list
            }
        
        # SCENARIO B: Default fallback (No container specified) -> List all containers
        containers = blob_service_client.list_containers()
        container_list = [c.name for c in containers]
        
        return {
            "StorageAccount": STORAGE_ACCOUNT_NAME,
            "ContainersFound": container_list
        }
        
    except Exception as e:
        return {"error": str(e)}