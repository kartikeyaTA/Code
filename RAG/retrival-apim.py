import requests
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery

# 1. Configurations
SEARCH_ENDPOINT = "https://txrhrag.search.windows.net"
INDEX_NAME = "kb-snow-index"
USER_ASSIGNED_CLIENT_ID = "6ece4a54-6f47-448a-8ca3-b675df1cfc1e"

APIM_EMBEDDING_URL = (
    "https://testing-apim-txrh.azure-api.net/models/openai/v1/embeddings"
)
API_KEY = "91c88533264a48858d06b82e4694fbc0"
MODEL_NAME = "text-embedding-3-small"


# 2. Helper to vectorize text query via APIM
def get_query_embedding(text: str) -> list:
    headers = {
        "Content-Type": "application/json",
        "api-key": API_KEY,
    }
    payload = {
        "model": MODEL_NAME,
        "input": text,
    }
    res = requests.post(
        APIM_EMBEDDING_URL, headers=headers, json=payload, timeout=10
    )
    res.raise_for_status()
    return res.json()["data"][0]["embedding"]


# 3. Initialize Search Client
credential = DefaultAzureCredential(
    managed_identity_client_id=USER_ASSIGNED_CLIENT_ID
)
search_client = SearchClient(
    endpoint=SEARCH_ENDPOINT, index_name=INDEX_NAME, credential=credential
)

# 4. Generate query vector via APIM
query_text = "Kartikeya's performance"
print(f"Generating vector for query: '{query_text}' via APIM...")
query_vector = get_query_embedding(query_text)

# 5. Execute Vector Search with raw floats (VectorizedQuery)
vector_query = VectorizedQuery(
    vector=query_vector,
    k_nearest_neighbors=1,
    fields="embedding_vector",
)

results = search_client.search(
    search_text=None,
    vector_queries=[vector_query],
    select=["sys_id", "number", "short_description", "description"],
    top=1,
)

# 6. Output Results
print("\n--- Search Results ---")
for doc in results:
    print(f"Score (@search.score): {doc.get('@search.score')}")
    print(f"sys_id: {doc.get('sys_id')}")
    print(f"number: {doc.get('number')}")
    print(f"short_description: {doc.get('short_description')}")
    print(f"description: {doc.get('description')}")
    print("-" * 30)