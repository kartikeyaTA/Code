from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient

SEARCH_ENDPOINT = "https://txrhrag.search.windows.net"
INDEX_NAME = "kb-snow-index"

search_client = SearchClient(
    endpoint=SEARCH_ENDPOINT,
    index_name=INDEX_NAME,
    credential=DefaultAzureCredential(),
)

# Pass a list containing the key dictionary with the action specified
target_sys_id = "5c9e4311bb801452aa009911e328002d"

results = search_client.delete_documents(
    documents=[{"sys_id": target_sys_id}]
)

print("Deletion Status:", results[0].succeeded)