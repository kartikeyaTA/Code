import os
from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient


def upload_folder_to_azure_blob(
    account_url: str,
    container_name: str,
    local_folder_path: str,
    user_assigned_client_id: str,
):
    """Uploads a local directory to Azure Blob Storage.

    Uses DefaultAzureCredential which automatically uses 'az login' locally, and
    Managed Identity when deployed to Azure.
    """
    # Pass the managed identity client_id to DefaultAzureCredential.
    # Locally: It falls back to Azure CLI ('az login').
    # In Azure: It uses the specified User-Assigned Managed Identity.
    credential = DefaultAzureCredential(
        managed_identity_client_id=user_assigned_client_id
    )

    blob_service_client = BlobServiceClient(
        account_url=account_url, credential=credential
    )

    container_client = blob_service_client.get_container_client(container_name)
    try:
        container_client.create_container()
        print(f"Created container: '{container_name}'")
    except ResourceExistsError:
        print(f"Container '{container_name}' already exists.")

    local_folder_path = os.path.abspath(local_folder_path)

    for root, _, files in os.walk(local_folder_path):
        for file_name in files:
            local_file_path = os.path.join(root, file_name)

            relative_path = os.path.relpath(
                local_file_path, start=local_folder_path
            )
            blob_name = relative_path.replace("\\", "/")

            blob_client = container_client.get_blob_client(blob_name)

            print(f"Uploading '{relative_path}' -> Blob: '{blob_name}'...")

            with open(local_file_path, "rb") as data:
                blob_client.upload_blob(data, overwrite=True)

    print("\nAll files successfully uploaded to Azure Blob Storage!")


if __name__ == "__main__":
    ACCOUNT_URL = "https://txrhragsnowkb.blob.core.windows.net"
    CONTAINER_NAME = "kb-snow-data"
    LOCAL_FOLDER = "./output"
    USER_ASSIGNED_CLIENT_ID = "6ece4a54-6f47-448a-8ca3-b675df1cfc1e"

    upload_folder_to_azure_blob(
        account_url=ACCOUNT_URL,
        container_name=CONTAINER_NAME,
        local_folder_path=LOCAL_FOLDER,
        user_assigned_client_id=USER_ASSIGNED_CLIENT_ID,
    )