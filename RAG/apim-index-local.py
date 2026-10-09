import os
import json
import time
import requests
from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.search.documents.indexes import (
    SearchIndexClient,
    SearchIndexerClient,
)
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    VectorSearch,
    VectorSearchProfile,
    HnswAlgorithmConfiguration,
    SearchIndexerDataSourceConnection,
    SearchIndexerDataContainer,
    SearchIndexerDataNoneIdentity,
    SearchIndexer,
    IndexingParameters,
    IndexingParametersConfiguration,
)

# =====================================================================
# Configuration Settings
# =====================================================================
SEARCH_ENDPOINT = "https://txrhrag.search.windows.net"

# APIM Endpoint & API Key (Matching your working curl command)
APIM_EMBEDDING_URL = "https://testing-apim-txrh.azure-api.net/models/openai/v1/embeddings"
API_KEY = "91c88533264a48858d06b82e4694fbc0"
EMBEDDING_MODEL_NAME = "text-embedding-3-small"

BLOB_ACCOUNT_URL = "https://txrhragsnowkb.blob.core.windows.net"
BLOB_CONTAINER_NAME = "kb-snow-data"

# Pipeline Object Names
DATA_SOURCE_NAME = "kb-blob-datasource"
INDEX_NAME = "kb-snow-index"
INDEXER_NAME = "kb-snow-indexer"

ACCOUNT_URL = "https://txrhragsnowkb.blob.core.windows.net"
CONTAINER_NAME = "kb-snow-data"
LOCAL_FOLDER = "./output"
USER_ASSIGNED_CLIENT_ID = "6ece4a54-6f47-448a-8ca3-b675df1cfc1e"

# Local Execution Credential Initialization
credential = DefaultAzureCredential()

index_client = SearchIndexClient(SEARCH_ENDPOINT, credential=credential)
indexer_client = SearchIndexerClient(SEARCH_ENDPOINT, credential=credential)


# =====================================================================
# Helper: Call APIM Endpoint via requests (Matches working curl logic)
# =====================================================================
def generate_embedding_via_apim(text: str) -> list:
    """Generates a vector embedding by posting directly to your working APIM endpoint."""
    headers = {
        "Content-Type": "application/json",
        "api-key": API_KEY,
    }
    payload = {
        "model": EMBEDDING_MODEL_NAME,
        "input": text,
    }

    response = requests.post(APIM_EMBEDDING_URL, headers=headers, json=payload, timeout=30)
    response.raise_for_status()

    return response.json()["data"][0]["embedding"]


# =====================================================================
# Step 1: Pre-Embed Documents Locally & Upload to Azure Blob Storage
# =====================================================================
def upload_folder_to_azure_blob():
    """Reads local JSON files, injects 'embedding_vector' via APIM, and uploads to Blob Storage."""
    blob_cred = DefaultAzureCredential(
        managed_identity_client_id=USER_ASSIGNED_CLIENT_ID
    )
    blob_service_client = BlobServiceClient(
        account_url=ACCOUNT_URL, credential=blob_cred
    )
    container_client = blob_service_client.get_container_client(CONTAINER_NAME)

    try:
        container_client.create_container()
        print(f"Created container: '{CONTAINER_NAME}'")
    except ResourceExistsError:
        print(f"Container '{CONTAINER_NAME}' already exists.")

    local_folder_path = os.path.abspath(LOCAL_FOLDER)

    for root, _, files in os.walk(local_folder_path):
        for file_name in files:
            if not file_name.endswith(".json"):
                continue

            local_file_path = os.path.join(root, file_name)
            relative_path = os.path.relpath(local_file_path, start=local_folder_path)
            blob_name = relative_path.replace("\\", "/")

            # Read document data
            with open(local_file_path, "r", encoding="utf-8") as f:
                doc_data = json.load(f)

            # Determine text string to embed
            text_to_embed = doc_data.get("embedding_text") or f"{doc_data.get('short_description', '')} {doc_data.get('description', '')}"

            print(f"Generating embedding via APIM for '{relative_path}'...")
            doc_data["embedding_vector"] = generate_embedding_via_apim(text_to_embed)

            # Upload JSON with vector included to Blob Storage
            blob_client = container_client.get_blob_client(blob_name)
            print(f"Uploading '{relative_path}' -> Blob: '{blob_name}'...")
            blob_client.upload_blob(json.dumps(doc_data), overwrite=True)

    print("\nAll files successfully pre-embedded and uploaded to Azure Blob Storage!")


# =====================================================================
# Step 2: Create Data Source Connection
# =====================================================================
def create_data_source():
    """Creates keyless data source connection to Blob Storage."""
    container = SearchIndexerDataContainer(name=BLOB_CONTAINER_NAME)
    identity = SearchIndexerDataNoneIdentity()

    resource_id = (
        "/subscriptions/c34b42f0-a89c-4e9d-9205-57ae6b91357f"
        "/resourceGroups/VisualStudioOnline-C88866B319C044FFB02C78F550AEF0FB"
        "/providers/Microsoft.Storage/storageAccounts/txrhragsnowkb"
    )

    data_source = SearchIndexerDataSourceConnection(
        name=DATA_SOURCE_NAME,
        type="azureblob",
        connection_string=f"ResourceId={resource_id};",
        container=container,
        identity=identity,
    )
    indexer_client.create_or_update_data_source_connection(data_source)
    print("Data Source connected keylessly to Blob Storage.")


# =====================================================================
# Step 3: Create Search Index (Direct Field Schema, No Skillset Needed)
# =====================================================================
def create_index():
    """Creates the Search Index with HNSW Vector Search capabilities."""
    fields = [
        SearchField(
            name="sys_id",
            type=SearchFieldDataType.String,
            key=True,
            filterable=True,
        ),
        SearchField(
            name="number",
            type=SearchFieldDataType.String,
            searchable=True,
            filterable=True,
        ),
        SearchField(
            name="short_description",
            type=SearchFieldDataType.String,
            searchable=True,
        ),
        SearchField(
            name="description",
            type=SearchFieldDataType.String,
            searchable=True,
        ),
        SearchField(
            name="kb_name",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
        ),
        SearchField(
            name="embedding_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=1536,
            vector_search_profile_name="myHnswProfile",
        ),
    ]

    vector_search = VectorSearch(
        profiles=[
            VectorSearchProfile(
                name="myHnswProfile",
                algorithm_configuration_name="myHnswConfig",
            )
        ],
        algorithms=[HnswAlgorithmConfiguration(name="myHnswConfig")],
    )

    index = SearchIndex(
        name=INDEX_NAME, fields=fields, vector_search=vector_search
    )
    index_client.create_or_update_index(index)
    print("Search Index created successfully.")


# =====================================================================
# Step 4: Create and Run Indexer
# =====================================================================
def create_and_run_indexer():
    """Creates and triggers execution of the Indexer."""
    parameters = IndexingParameters(
        configuration=IndexingParametersConfiguration(parsing_mode="json")
    )

    indexer = SearchIndexer(
        name=INDEXER_NAME,
        data_source_name=DATA_SOURCE_NAME,
        target_index_name=INDEX_NAME,
        parameters=parameters,
    )

    indexer_client.create_or_update_indexer(indexer)
    print("Indexer created. Running ingestion pipeline...")
    indexer_client.run_indexer(INDEXER_NAME)
    print("Indexer triggered successfully!")


# =====================================================================
# Step 5: Wait for Indexer Completion
# =====================================================================
def wait_for_indexer_completion():
    """Polls until indexing is finished."""
    print("Waiting for indexer to complete processing blobs...")
    while True:
        status = indexer_client.get_indexer_status(INDEXER_NAME)
        last_result = status.last_result

        if last_result:
            execution_status = last_result.status.lower()
            if execution_status in [
                "success",
                "transientfailure",
                "persistentfailure",
            ]:
                print(
                    f"Indexer execution finished with status: '{last_result.status}'."
                )
                print(f"Processed items: {last_result.item_count}")
                break

        time.sleep(5)


# =====================================================================
# Step 6: Delete Blobs from Azure Storage
# =====================================================================
def delete_blobs_from_container():
    """Cleans up temporary files from Blob storage."""
    blob_cred = DefaultAzureCredential(
        managed_identity_client_id=USER_ASSIGNED_CLIENT_ID
    )
    blob_service_client = BlobServiceClient(
        account_url=ACCOUNT_URL, credential=blob_cred
    )
    container_client = blob_service_client.get_container_client(
        CONTAINER_NAME
    )

    print("\nDeleting temporary blobs from Azure Storage...")
    blobs = list(container_client.list_blobs())

    if not blobs:
        print("No blobs found to delete.")
        return

    for blob in blobs:
        container_client.delete_blob(blob.name)
        print(f"Deleted blob: '{blob.name}'")

    print("All temporary blob data deleted successfully!")


if __name__ == "__main__":
    upload_folder_to_azure_blob()
    create_data_source()
    create_index()
    create_and_run_indexer()
    wait_for_indexer_completion()
    delete_blobs_from_container()