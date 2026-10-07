import json
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizableTextQuery

# 1. Configuration
SEARCH_ENDPOINT = "https://txrhrag.search.windows.net"
INDEX_NAME = "kb-snow-index"
USER_ASSIGNED_CLIENT_ID = "6ece4a54-6f47-448a-8ca3-b675df1cfc1e"

# 2. Authenticate keylessly
credential = DefaultAzureCredential(
    managed_identity_client_id=USER_ASSIGNED_CLIENT_ID
)

# 3. Initialize SearchClient
search_client = SearchClient(
    endpoint=SEARCH_ENDPOINT, index_name=INDEX_NAME, credential=credential
)

# 4. Define the Vectorizable Text Query (matches "vectorQueries" in Portal JSON)
vector_query = VectorizableTextQuery(
    text="Rohit's Recovery from Injury",
    fields="embedding_vector",
    k_nearest_neighbors=1,  # Matches "k": 1
)

# 5. Execute Search (matches "select" and "top": 1 in Portal JSON)
results = search_client.search(
    search_text=None,  # Pure vector search (no keyword search)
    vector_queries=[vector_query],
    select=["sys_id", "number", "short_description", "description"],
    top=1,
)

# 6. Parse and Print Results
print("--- Search Results ---")
for doc in results:
    print(f"Search Score (@search.score): {doc.get('@search.score')}")
    print(f"sys_id: {doc.get('sys_id')}")
    print(f"number: {doc.get('number')}")
    print(f"short_description: {doc.get('short_description')}")
    print(f"description: {doc.get('description')}")
    print("-" * 30)