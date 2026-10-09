import os
import time
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

# =====================================================================
# Configuration Settings
# =====================================================================
SEARCH_ENDPOINT = "https://txrhrag.search.windows.net"

# APIM gateway (NOT the openai.azure.com URL)
APIM_ENDPOINT = "https://testing-apim-txrh.azure-api.net/models"
# Set before running:  export APIM_SUBSCRIPTION_KEY="<new regenerated key>"
APIM_SUBSCRIPTION_KEY ="91c88533264a48858d06b82e4694fbc0"
EMBEDDING_DEPLOYMENT_NAME = "text-embedding-3-small"

ACCOUNT_URL = "https://txrhragsnowkb.blob.core.windows.net"
CONTAINER_NAME = "kb-snow-data"
LOCAL_FOLDER = "./output"
USER_ASSIGNED_CLIENT_ID = "6ece4a54-6f47-448a-8ca3-b675df1cfc1e"

# Pipeline Object Names
DATA_SOURCE_NAME = "kb-blob-datasource"
SKILLSET_NAME = "kb-embedding-skillset"
INDEX_NAME = "kb-snow-index"
INDEXER_NAME = "kb-snow-indexer"

credential = DefaultAzureCredential()
index_client = SearchIndexClient(SEARCH_ENDPOINT, credential=credential)
indexer_client = SearchIndexerClient(SEARCH_ENDPOINT, credential=credential)


def _blob_container_client():
    blob_cred = DefaultAzureCredential(
        managed_identity_client_id=USER_ASSIGNED_CLIENT_ID
    )
    service = BlobServiceClient(account_url=ACCOUNT_URL, credential=blob_cred)
    return service.get_container_client(CONTAINER_NAME)


def upload_folder_to_azure_blob():
    """Uploads a local directory to Azure Blob Storage."""
    container_client = _blob_container_client()
    try:
        container_client.create_container()
        print(f"Created container: '{CONTAINER_NAME}'")
    except ResourceExistsError:
        print(f"Container '{CONTAINER_NAME}' already exists.")

    local_folder_path = os.path.abspath(LOCAL_FOLDER)
    for root, _, files in os.walk(local_folder_path):
        for file_name in files:
            local_file_path = os.path.join(root, file_name)
            relative_path = os.path.relpath(local_file_path, start=local_folder_path)
            blob_name = relative_path.replace("\\", "/")
            print(f"Uploading '{relative_path}' -> Blob: '{blob_name}'...")
            with open(local_file_path, "rb") as data:
                container_client.get_blob_client(blob_name).upload_blob(
                    data, overwrite=True
                )
    print("\nAll files successfully uploaded to Azure Blob Storage!")


# Step 1: Data Source (unchanged - Search -> Blob still uses managed identity)
def create_data_source():
    resource_id = (
        "/subscriptions/c34b42f0-a89c-4e9d-9205-57ae6b91357f"
        "/resourceGroups/VisualStudioOnline-C88866B319C044FFB02C78F550AEF0FB"
        "/providers/Microsoft.Storage/storageAccounts/txrhragsnowkb"
    )
    data_source = SearchIndexerDataSourceConnection(
        name=DATA_SOURCE_NAME,
        type="azureblob",
        connection_string=f"ResourceId={resource_id};",
        container=SearchIndexerDataContainer(name=CONTAINER_NAME),
        identity=SearchIndexerDataNoneIdentity(),
    )
    indexer_client.create_or_update_data_source_connection(data_source)
    print("Data Source connected keylessly to Blob Storage.")


# Step 2: Skillset -> APIM with subscription key
def create_skillset():
    embedding_skill = AzureOpenAIEmbeddingSkill(
        description="Generates embeddings via Azure OpenAI behind APIM",
        resource_url=APIM_ENDPOINT,
        deployment_name=EMBEDDING_DEPLOYMENT_NAME,
        model_name=EMBEDDING_DEPLOYMENT_NAME,
        api_key=APIM_SUBSCRIPTION_KEY,        # APIM subscription key; no auth_identity
        inputs=[
            InputFieldMappingEntry(name="text", source="/document/embedding_text")
        ],
        outputs=[
            OutputFieldMappingEntry(name="embedding", target_name="embedding_vector")
        ],
    )
    skillset = SearchIndexerSkillset(
        name=SKILLSET_NAME,
        description="Embedding skillset via APIM",
        skills=[embedding_skill],
    )
    indexer_client.create_or_update_skillset(skillset)
    print("Skillset created using APIM endpoint.")


# Step 3: Index -> vectorizer also via APIM
def create_index():
    fields = [
        SearchField(name="sys_id", type=SearchFieldDataType.String,
                    key=True, filterable=True),
        SearchField(name="number", type=SearchFieldDataType.String,
                    searchable=True, filterable=True),
        SearchField(name="short_description", type=SearchFieldDataType.String,
                    searchable=True),
        SearchField(name="description", type=SearchFieldDataType.String,
                    searchable=True),
        SearchField(name="kb_name", type=SearchFieldDataType.String,
                    filterable=True, facetable=True),
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

    index_client.create_or_update_index(
        SearchIndex(name=INDEX_NAME, fields=fields, vector_search=vector_search)
    )
    print("Search Index created.")


# Step 4: Indexer (unchanged)
def create_and_run_indexer():
    indexer = SearchIndexer(
        name=INDEXER_NAME,
        data_source_name=DATA_SOURCE_NAME,
        target_index_name=INDEX_NAME,
        skillset_name=SKILLSET_NAME,
        parameters=IndexingParameters(
            configuration=IndexingParametersConfiguration(parsing_mode="json")
        ),
        output_field_mappings=[
            FieldMapping(
                source_field_name="/document/embedding_vector",
                target_field_name="embedding_vector",
            )
        ],
    )
    indexer_client.create_or_update_indexer(indexer)
    print("Indexer created. Running ingestion pipeline...")
    indexer_client.run_indexer(INDEXER_NAME)
    print("Indexer triggered successfully!")


# Step 5: Wait for completion (unchanged, plus error output)
def wait_for_indexer_completion():
    print("Waiting for indexer to complete processing blobs...")
    while True:
        last_result = indexer_client.get_indexer_status(INDEXER_NAME).last_result
        if last_result:
            status = last_result.status.lower()
            if status in ["success", "transientfailure", "persistentfailure"]:
                print(f"Indexer finished with status: '{last_result.status}'.")
                print(f"Processed items: {last_result.item_count}")
                print(f"Failed items: {last_result.failed_item_count}")
                if last_result.error_message:
                    print(f"Error: {last_result.error_message}")
                for err in (last_result.errors or [])[:5]:
                    print(f" - {err.key}: {err.error_message}")
                return status == "success"
        time.sleep(5)


# Step 6: Delete blobs (unchanged)
def delete_blobs_from_container():
    container_client = _blob_container_client()
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
    create_skillset()
    create_index()
    create_and_run_indexer()

    # Only delete source blobs if indexing actually succeeded
    if wait_for_indexer_completion():
        delete_blobs_from_container()
    else:
        print("Indexing did not succeed - blobs kept for troubleshooting.")