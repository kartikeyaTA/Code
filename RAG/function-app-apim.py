import json
import logging
import os
import time
import azure.functions as func
import requests
from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import (
    SearchIndexClient,
    SearchIndexerClient,
)
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    IndexingParameters,
    IndexingParametersConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchIndexer,
    SearchIndexerDataContainer,
    SearchIndexerDataNoneIdentity,
    SearchIndexerDataSourceConnection,
    VectorSearch,
    VectorSearchProfile,
)
from azure.storage.blob import BlobServiceClient

# =====================================================================
# Configuration Settings
# =====================================================================
SEARCH_ENDPOINT = "https://txrhrag.search.windows.net"

# APIM Direct Endpoint & API Key (Matching your working curl)
APIM_EMBEDDING_URL = (
    "https://testing-apim-txrh.azure-api.net/models/openai/v1/embeddings"
)
API_KEY = "91c88533264a48858d06b82e4694fbc0"
EMBEDDING_MODEL_NAME = "text-embedding-3-small"

BLOB_ACCOUNT_URL = "https://txrhragsnowkb.blob.core.windows.net"
BLOB_CONTAINER_NAME = "kb-snow-data"

DATA_SOURCE_NAME = "kb-blob-datasource"
INDEX_NAME = "kb-snow-index"
INDEXER_NAME = "kb-snow-indexer"

USER_ASSIGNED_CLIENT_ID = "6ece4a54-6f47-448a-8ca3-b675df1cfc1e"
LOCAL_FOLDER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "output"
)

app = func.FunctionApp()


# =====================================================================
# Helper: Vectorize text via APIM Direct HTTP POST
# =====================================================================
def generate_embedding_via_apim(text: str) -> list:
    """Generates vector embedding by calling APIM endpoint directly."""
    headers = {
        "Content-Type": "application/json",
        "api-key": API_KEY,
    }
    payload = {
        "model": EMBEDDING_MODEL_NAME,
        "input": text,
    }
    response = requests.post(
        APIM_EMBEDDING_URL, headers=headers, json=payload, timeout=30
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


# =====================================================================
# Pipeline Core Execution Logic
# =====================================================================
def run_rag_pipeline():
    logging.info("Starting RAG Ingestion Pipeline in Function App...")

    credential = DefaultAzureCredential(
        managed_identity_client_id=USER_ASSIGNED_CLIENT_ID
    )

    index_client = SearchIndexClient(SEARCH_ENDPOINT, credential=credential)
    indexer_client = SearchIndexerClient(
        SEARCH_ENDPOINT, credential=credential
    )
    blob_service_client = BlobServiceClient(
        account_url=BLOB_ACCOUNT_URL, credential=credential
    )

    # -----------------------------------------------------------------
    # Step 1: Pre-Embed Local JSON Documents & Upload to Azure Blob Storage
    # -----------------------------------------------------------------
    container_client = blob_service_client.get_container_client(
        BLOB_CONTAINER_NAME
    )
    try:
        container_client.create_container()
        logging.info(f"Created container: '{BLOB_CONTAINER_NAME}'")
    except ResourceExistsError:
        logging.info(f"Container '{BLOB_CONTAINER_NAME}' already exists.")

    if os.path.exists(LOCAL_FOLDER):
        for root, _, files in os.walk(LOCAL_FOLDER):
            for file_name in files:
                if not file_name.endswith(".json"):
                    continue

                local_file_path = os.path.join(root, file_name)
                relative_path = os.path.relpath(
                    local_file_path, start=LOCAL_FOLDER
                )
                blob_name = relative_path.replace("\\", "/")

                with open(local_file_path, "r", encoding="utf-8") as f:
                    doc_data = json.load(f)

                # Determine string to embed
                text_to_embed = doc_data.get("embedding_text") or (
                    f"{doc_data.get('short_description', '')} "
                    f"{doc_data.get('description', '')}"
                )

                logging.info(
                    f"Generating embedding via APIM for '{relative_path}'..."
                )
                doc_data["embedding_vector"] = generate_embedding_via_apim(
                    text_to_embed
                )

                blob_client = container_client.get_blob_client(blob_name)
                logging.info(
                    f"Uploading '{relative_path}' -> Blob: '{blob_name}'..."
                )
                blob_client.upload_blob(json.dumps(doc_data), overwrite=True)

        logging.info(
            "All files successfully pre-embedded and uploaded to Azure Blob Storage!"
        )

    # -----------------------------------------------------------------
    # Step 2: Create / Update Data Source
    # -----------------------------------------------------------------
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
    logging.info("Data Source connected keylessly to Blob Storage.")

    # -----------------------------------------------------------------
    # Step 3: Create / Update Search Index
    # -----------------------------------------------------------------
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
    logging.info("Search Index created successfully.")

    # -----------------------------------------------------------------
    # Step 4: Create and Trigger Indexer
    # -----------------------------------------------------------------
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
    logging.info("Indexer created. Running ingestion pipeline...")
    indexer_client.run_indexer(INDEXER_NAME)

    # -----------------------------------------------------------------
    # Step 5: Wait for Indexer Completion
    # -----------------------------------------------------------------
    logging.info("Waiting for indexer to complete processing blobs...")
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
                logging.info(
                    f"Indexer execution finished with status: '{last_result.status}'."
                )
                logging.info(f"Processed items: {last_result.item_count}")
                break

        time.sleep(5)

    # -----------------------------------------------------------------
    # Step 6: Delete Temporary Blobs from Azure Storage
    # -----------------------------------------------------------------
    logging.info("Deleting temporary blobs from Azure Storage...")
    blobs = list(container_client.list_blobs())

    if not blobs:
        logging.info("No blobs found to delete.")
        return

    for blob in blobs:
        container_client.delete_blob(blob.name)
        logging.info(f"Deleted blob: '{blob.name}'")

    logging.info("All temporary blob data deleted successfully!")


# =====================================================================
# Scheduled Timer Trigger (Runs Daily at 10:25 AM UTC)
# =====================================================================
@app.schedule(
    schedule="0 37 10 * * *",
    arg_name="myTimer",
    run_on_startup=False,
    use_monitor=False,
)
def ScheduledRAGIndexer(myTimer: func.TimerRequest) -> None:
    if myTimer.past_due:
        logging.info("The timer is past due!")

    logging.info("Scheduled RAG Indexer Timer triggered.")
    try:
        run_rag_pipeline()
        logging.info("Pipeline executed successfully!")
    except Exception as e:
        logging.error(f"Execution failed: {e}")