import logging
import os
import time
from datetime import datetime, timedelta, timezone

import azure.functions as func
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
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
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    VectorSearch,
    VectorSearchProfile,
)
from azure.storage.blob import BlobServiceClient

# 1. MUST BE AT TOP-LEVEL: initialize app
app = func.FunctionApp()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_FOLDER = os.path.join(SCRIPT_DIR, "output")

# Fetch Environment Variables
SEARCH_ENDPOINT = os.environ.get("SEARCH_ENDPOINT", "")
APIM_ENDPOINT = os.environ.get("APIM_ENDPOINT", "")
APIM_SUBSCRIPTION_KEY = os.environ.get("APIM_SUBSCRIPTION_KEY")
EMBEDDING_DEPLOYMENT_NAME = os.environ.get("EMBEDDING_DEPLOYMENT_NAME", "")
# NEW: the underlying MODEL name (e.g. text-embedding-3-small), not the deployment name.
# If left empty it falls back to the deployment name, which is the old behavior.
EMBEDDING_MODEL_NAME = os.environ.get("EMBEDDING_MODEL_NAME") or EMBEDDING_DEPLOYMENT_NAME

BLOB_ACCOUNT_URL = os.environ.get("BLOB_ACCOUNT_URL", "")
BLOB_CONTAINER_NAME = os.environ.get("BLOB_CONTAINER_NAME", "")
BLOB_RESOURCE_ID = os.environ.get("BLOB_RESOURCE_ID", "")

DATA_SOURCE_NAME = os.environ.get("DATA_SOURCE_NAME", "")
SKILLSET_NAME = os.environ.get("SKILLSET_NAME", "")
INDEX_NAME = os.environ.get("INDEX_NAME", "")
INDEXER_NAME = os.environ.get("INDEXER_NAME", "")

# NEW: semantic configuration name (used as semantic_configuration_name at query time)
SEMANTIC_CONFIG_NAME = os.environ.get("SEMANTIC_CONFIG_NAME", "default")

# NEW: set REBUILD_INDEX=true for ONE run after changing field attributes or adding
# searchable fields. Azure AI Search cannot change an existing field's attributes in
# place, so the index is deleted and recreated and the indexer is reset so every
# blob is re-processed. Set it back to false afterwards.
REBUILD_INDEX = os.environ.get("REBUILD_INDEX", "false").lower() == "true"

INDEXER_MAX_WAIT_SECONDS = 30 * 60


def build_index() -> SearchIndex:
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
        # CHANGED: facetable removed (not needed unless you build facet filters)
        SearchField(
            name="kb_name",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        # NEW: the text that was embedded. Searchable (keyword search + semantic ranker)
        # but hidden = not returned in results. It is read from the blob JSON by name.
        SearchField(
            name="embedding_text",
            type=SearchFieldDataType.String,
            searchable=True,
            hidden=True,
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
                    model_name=EMBEDDING_MODEL_NAME,  # CHANGED: model name
                    api_key=APIM_SUBSCRIPTION_KEY,
                ),
            )
        ],
    )

    # NEW: semantic configuration -> enables @search.reranker_score at query time
    semantic_search = SemanticSearch(
        default_configuration_name=SEMANTIC_CONFIG_NAME,
        configurations=[
            SemanticConfiguration(
                name=SEMANTIC_CONFIG_NAME,
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="short_description"),
                    content_fields=[SemanticField(field_name="embedding_text")],
                ),
            )
        ],
    )

    return SearchIndex(
        name=INDEX_NAME,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )


def run_rag_pipeline():
    logging.info("Starting Keyless RAG Ingestion Pipeline...")

    credential = DefaultAzureCredential()

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
    data_source = SearchIndexerDataSourceConnection(
        name=DATA_SOURCE_NAME,
        type="azureblob",
        connection_string=f"ResourceId={BLOB_RESOURCE_ID};",
        container=container,
        identity=identity,
    )
    indexer_client.create_or_update_data_source_connection(data_source)

    # 3. Create Skillset
    embedding_skill = AzureOpenAIEmbeddingSkill(
        description="Generates vector embeddings keylessly via Azure AI Foundry",
        resource_url=APIM_ENDPOINT,
        deployment_name=EMBEDDING_DEPLOYMENT_NAME,
        model_name=EMBEDDING_MODEL_NAME,  # CHANGED: model name
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
    logging.info("Skillset created using APIM endpoint.")

    # 4. Create Index (hybrid + semantic ready)
    if REBUILD_INDEX:
        logging.warning(
            "REBUILD_INDEX=true: deleting index '%s' before recreating it.", INDEX_NAME
        )
        try:
            index_client.delete_index(INDEX_NAME)
        except ResourceNotFoundError:
            pass
    index_client.create_or_update_index(build_index())
    logging.info("Search Index created/updated (hybrid + semantic).")

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
    if REBUILD_INDEX:
        # New empty index: re-process every blob, not just changed ones
        indexer_client.reset_indexer(INDEXER_NAME)
    run_started = datetime.now(timezone.utc)
    indexer_client.run_indexer(INDEXER_NAME)

    # 6. Wait for THIS run to finish (ignore the previous run's result)
    deadline = time.time() + INDEXER_MAX_WAIT_SECONDS
    while True:
        status = indexer_client.get_indexer_status(INDEXER_NAME)
        last_result = status.last_result
        if (
            last_result
            and last_result.start_time
            and last_result.start_time >= run_started - timedelta(seconds=30)
            and (last_result.status or "").lower()
            in ["success", "transientfailure", "persistentfailure"]
        ):
            logging.info(f"Indexer execution finished with status: '{last_result.status}'.")
            break
        if time.time() > deadline:
            raise TimeoutError("Indexer did not finish in time; blobs left in place.")
        time.sleep(5)

    # Keep the blobs if the indexer failed so you can debug / re-run
    if (last_result.status or "").lower() != "success":
        errors = [
            f"{getattr(e, 'key', '')}: {getattr(e, 'error_message', '')}"
            for e in (last_result.errors or [])[:5]
        ]
        raise RuntimeError(
            f"Indexer finished with '{last_result.status}': "
            f"{last_result.error_message} {errors}. Blobs left in place."
        )

    # 7. Clean up Temporary Blobs
    logging.info("Waiting for 60 seconds before cleaning up temporary blobs...")
    time.sleep(60)
    logging.info("Cleaning up temporary blobs...")
    blobs = list(container_client.list_blobs())
    for blob in blobs:
        container_client.delete_blob(blob.name)
    logging.info("Temporary blobs deleted successfully!")


# 2. REGISTERED VIA @app.schedule
@app.schedule(
    schedule="0 10 12 * * *",
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
        logging.exception(f"Execution failed: {e}")
        raise  # surface the failure in Invocations / Application Insights
