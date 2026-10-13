import logging
import os
import time
import azure.functions as func
from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import (
    SearchIndexClient,
    SearchIndexerClient,
)
from azure.search.documents.indexes.models import (
    AzureOpenAIEmbeddingSkill,
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
    FieldMapping,
    HnswAlgorithmConfiguration,
    IndexingParameters,
    IndexingParametersConfiguration,
    InputFieldMappingEntry,
    OutputFieldMappingEntry,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchIndexer,
    SearchIndexerDataContainer,
    SearchIndexerDataNoneIdentity,
    SearchIndexerDataSourceConnection,
    SearchIndexerSkillset,
    VectorSearch,
    VectorSearchProfile,
)
from azure.storage.blob import BlobServiceClient

# 1. MUST BE AT TOP-LEVEL: initialize app
app = func.FunctionApp()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_FOLDER = os.path.join(SCRIPT_DIR, "output")

SEARCH_ENDPOINT = "https://txrhrag.search.windows.net"
APIM_ENDPOINT = "https://testing-apim-txrh.azure-api.net/models"
# Set before running:  export APIM_SUBSCRIPTION_KEY="<new regenerated key>"
APIM_SUBSCRIPTION_KEY ="91c88533264a48858d06b82e4694fbc0"
EMBEDDING_DEPLOYMENT_NAME = "text-embedding-3-small"

BLOB_ACCOUNT_URL = "https://txrhragsnowkb.blob.core.windows.net"
BLOB_CONTAINER_NAME = "kb-snow-data"

DATA_SOURCE_NAME = "kb-blob-datasource"
SKILLSET_NAME = "kb-embedding-skillset"
INDEX_NAME = "kb-snow-index"
INDEXER_NAME = "kb-snow-indexer"

USER_ASSIGNED_CLIENT_ID = "6ece4a54-6f47-448a-8ca3-b675df1cfc1e"


def run_rag_pipeline():
    logging.info("Starting Keyless RAG Ingestion Pipeline...")

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

    # 1. Upload output/ folder contents to Blob Storage
    container_client = blob_service_client.get_container_client(
        BLOB_CONTAINER_NAME
    )
    try:
        container_client.create_container()
    except ResourceExistsError:
        pass

    if os.path.exists(LOCAL_FOLDER):
        for root, _, files in os.walk(LOCAL_FOLDER):
            for file_name in files:
                local_file_path = os.path.join(root, file_name)
                relative_path = os.path.relpath(
                    local_file_path, start=LOCAL_FOLDER
                )
                blob_name = relative_path.replace("\\", "/")

                blob_client = container_client.get_blob_client(blob_name)
                with open(local_file_path, "rb") as data:
                    blob_client.upload_blob(data, overwrite=True)
        logging.info("Uploaded output files to Azure Blob Storage.")

    # 2. Create Data Source
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

    # 3. Create Skillset
    embedding_skill = AzureOpenAIEmbeddingSkill(
        description="Generates vector embeddings keylessly via Azure AI Foundry",
        resource_url=APIM_ENDPOINT,
        deployment_name=EMBEDDING_DEPLOYMENT_NAME,
        model_name=EMBEDDING_DEPLOYMENT_NAME,
        api_key=APIM_SUBSCRIPTION_KEY,
        inputs=[
            InputFieldMappingEntry(
                name="text", source="/document/embedding_text"
            )
        ],
        outputs=[
            OutputFieldMappingEntry(
                name="embedding", target_name="embedding_vector"
            )
        ],
    )
    skillset = SearchIndexerSkillset(
        name=SKILLSET_NAME,
        description="Keyless skillset for embedding generation",
        skills=[embedding_skill],
    )
    indexer_client.create_or_update_skillset(skillset)
    print("Skillset created using APIM endpoint.")
    
    # 4. Create Index
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
                vectorizer_name="myVectorizer",
            )
        ],
        algorithms=[HnswAlgorithmConfiguration(name="myHnswConfig")],
        vectorizers=[
            AzureOpenAIVectorizer(
                vectorizer_name="myVectorizer",
                parameters=AzureOpenAIVectorizerParameters(
                    resource_url=APIM_ENDPOINT,
                    deployment_name=EMBEDDING_DEPLOYMENT_NAME,
                    model_name=EMBEDDING_DEPLOYMENT_NAME,
                    api_key=APIM_SUBSCRIPTION_KEY,
                ),
            )
        ],
    )
    index = SearchIndex(
        name=INDEX_NAME, fields=fields, vector_search=vector_search
    )
    index_client.create_or_update_index(index)
    print("Search Index created.")
    
    # 5. Run Indexer
    parameters = IndexingParameters(
        configuration=IndexingParametersConfiguration(parsing_mode="json")
    )
    output_mappings = [
        FieldMapping(
            source_field_name="/document/embedding_vector",
            target_field_name="embedding_vector",
        )
    ]
    indexer = SearchIndexer(
        name=INDEXER_NAME,
        data_source_name=DATA_SOURCE_NAME,
        target_index_name=INDEX_NAME,
        skillset_name=SKILLSET_NAME,
        parameters=parameters,
        output_field_mappings=output_mappings,
    )
    indexer_client.create_or_update_indexer(indexer)
    indexer_client.run_indexer(INDEXER_NAME)

    # 6. Wait for Completion
    while True:
        status = indexer_client.get_indexer_status(INDEXER_NAME)
        last_result = status.last_result
        if status.status.lower() != "in-progress" and last_result:
            execution_status = last_result.status.lower()
            if execution_status in ["success", "transientfailure", "persistentfailure"]:
                logging.info(f"Indexer execution finished with status: '{last_result.status}'.")
                break
        time.sleep(5)

    # 7. Clean up Temporary Blobs
    logging.info("Waiting for 60 seconds before cleaning up temporary blobs...")
    time.sleep(60)
    logging.info("Cleaning up temporary blobs...")
    blobs = list(container_client.list_blobs())
    for blob in blobs:
        container_client.delete_blob(blob.name)
    logging.info("Temporary blobs deleted successfully!")


# 2. MUST BE REGISTERED VIA @app.schedule
@app.schedule(
    schedule="0 22 6 * * *",
    arg_name="myTimer",
    run_on_startup=False,
    use_monitor=False,
)
def ScheduledRAGIndexer(myTimer: func.TimerRequest) -> None:
    if myTimer.past_due:
        logging.info("The timer is past due!")

    logging.info("Scheduled Timer Trigger Executing...")
    try:
        run_rag_pipeline()
        logging.info("RAG Pipeline execution finished successfully!")
    except Exception as e:
        logging.error(f"Execution failed: {e}")