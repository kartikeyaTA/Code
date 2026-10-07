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
FOUNDRY_ENDPOINT = "https://voice-open-ai-txrh.openai.azure.com/"
EMBEDDING_DEPLOYMENT_NAME = "text-embedding-3-small"

BLOB_ACCOUNT_URL = "https://txrhragsnowkb.blob.core.windows.net"
BLOB_CONTAINER_NAME = "kb-snow-data"

# Pipeline Object Names
DATA_SOURCE_NAME = "kb-blob-datasource"
SKILLSET_NAME = "kb-embedding-skillset"
INDEX_NAME = "kb-snow-index"
INDEXER_NAME = "kb-snow-indexer"

ACCOUNT_URL = "https://txrhragsnowkb.blob.core.windows.net"
CONTAINER_NAME = "kb-snow-data"
LOCAL_FOLDER = "./output"
USER_ASSIGNED_CLIENT_ID = "6ece4a54-6f47-448a-8ca3-b675df1cfc1e"

# Keyless Credential Initialization using active 'az login' locally
credential = DefaultAzureCredential()

index_client = SearchIndexClient(SEARCH_ENDPOINT, credential=credential)
indexer_client = SearchIndexerClient(SEARCH_ENDPOINT, credential=credential)


def upload_folder_to_azure_blob():
    """Uploads a local directory to Azure Blob Storage."""
    blob_cred = DefaultAzureCredential(
        managed_identity_client_id=USER_ASSIGNED_CLIENT_ID
    )

    blob_service_client = BlobServiceClient(
        account_url=ACCOUNT_URL, credential=blob_cred
    )

    container_client = blob_service_client.get_container_client(
        CONTAINER_NAME
    )
    try:
        container_client.create_container()
        print(f"Created container: '{CONTAINER_NAME}'")
    except ResourceExistsError:
        print(f"Container '{CONTAINER_NAME}' already exists.")

    local_folder_path = os.path.abspath(LOCAL_FOLDER)

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


# Step 1: Create Data Source Connection
def create_data_source():
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


# Step 2: Create Skillset for Auto-Vectorization
def create_skillset():
    embedding_skill = AzureOpenAIEmbeddingSkill(
        description="Generates vector embeddings keylessly via Azure AI Foundry",
        resource_url=FOUNDRY_ENDPOINT,
        deployment_name=EMBEDDING_DEPLOYMENT_NAME,
        model_name=EMBEDDING_DEPLOYMENT_NAME,
        auth_identity=SearchIndexerDataNoneIdentity(),
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
    print("Skillset created using keyless Azure AI Foundry connection.")


# Step 3: Create Search Index
def create_index():
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
                    resource_url=FOUNDRY_ENDPOINT,
                    deployment_name=EMBEDDING_DEPLOYMENT_NAME,
                    model_name=EMBEDDING_DEPLOYMENT_NAME,
                ),
            )
        ],
    )

    index = SearchIndex(
        name=INDEX_NAME, fields=fields, vector_search=vector_search
    )
    index_client.create_or_update_index(index)
    print("Search Index created keylessly.")


# Step 4: Create and Run Indexer
def create_and_run_indexer():
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
    print("Indexer created. Running keyless ingestion pipeline...")
    indexer_client.run_indexer(INDEXER_NAME)
    print("Indexer triggered successfully!")


# Step 5: Wait for Indexer Completion
def wait_for_indexer_completion():
    print("Waiting for indexer to complete processing blobs...")
    while True:
        status = indexer_client.get_indexer_status(INDEXER_NAME)
        last_result = status.last_result

        if last_result:
            execution_status = last_result.status.lower()
            if execution_status in ["success", "transientfailure", "persistentfailure"]:
                print(
                    f"Indexer execution finished with status: '{last_result.status}'."
                )
                print(f"Processed items: {last_result.item_count}")
                break

        time.sleep(5)


# Step 6: Delete Blobs from Azure Storage
def delete_blobs_from_container():
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
    create_skillset()
    create_index()
    create_and_run_indexer()

    # Wait for indexing to complete, then clean up blob storage
    wait_for_indexer_completion()
    delete_blobs_from_container()