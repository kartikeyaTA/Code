from concurrent.futures import ThreadPoolExecutor

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizableTextQuery

# 1. Configuration
SEARCH_ENDPOINT = "https://txrhrag.search.windows.net"
USER_ASSIGNED_CLIENT_ID = "6ece4a54-6f47-448a-8ca3-b675df1cfc1e"

MIN_SCORE = 1.5          # minimum @search.reranker_score (0-4 scale) to be considered relevant
FINAL_TOP_K = 5          # max results sent to the agent
TIMEOUT_SEC = 5          # max wait per index

# 2. One entry per index. Add the SharePoint chunk index here later.
SOURCES = [
    {
        "name": "snow-kb",
        "index": "kb-snow-index",
        "top": 1,
        "vector_field": "embedding_vector",
        "semantic_config": "default",
        "search_fields": ["embedding_text", "short_description", "number"],
        "select": ["sys_id", "number", "short_description", "description"],
        "filter": None,   # TODO: access filter for the signed-in user
    },
    # {
    #     "name": "sharepoint-chunks",
    #     "index": "sharepoint-chunks",
    #     "top": 5,
    #     "vector_field": "contentVector",
    #     "semantic_config": "default",
    #     "search_fields": ["embedding_text", "title"],
    #     "select": ["chunk_id", "file_name", "web_url", "content"],
    #     "filter": None,
    # },
]

# 3. Authenticate keylessly (same as before)
credential = DefaultAzureCredential(managed_identity_client_id=USER_ASSIGNED_CLIENT_ID)


# 4. Your query, wrapped in a function that takes one index config
def search_one(src, query):
    client = SearchClient(endpoint=SEARCH_ENDPOINT, index_name=src["index"], credential=credential)

    vector_query = VectorizableTextQuery(
        text=query,
        fields=src["vector_field"],
        k_nearest_neighbors=50,
    )

    results = client.search(
        search_text=query,                          # keyword side (hybrid)
        search_fields=src["search_fields"],
        vector_queries=[vector_query],
        query_type="semantic",                      # semantic ranker on
        semantic_configuration_name=src["semantic_config"],
        filter=src["filter"],
        select=src["select"],
        top=src["top"],
    )

    hits = []
    for doc in results:
        hit = {k: v for k, v in doc.items() if not k.startswith("@")}
        hit["source"] = src["name"]
        hit["score"] = doc.get("@search.reranker_score") or 0.0
        hits.append(hit)
    return hits


# 5. Run all indexes in parallel, pool, threshold, keep top K
def retrieve(query):
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        futures = [(src, pool.submit(search_one, src, query)) for src in SOURCES]

        pooled = []
        for src, fut in futures:
            try:
                pooled.extend(fut.result(timeout=TIMEOUT_SEC))
            except Exception as exc:               # one index failing must not fail the request
                print(f"[{src['name']}] failed: {exc}")

    pooled.sort(key=lambda h: h["score"], reverse=True)
    return pooled


if __name__ == "__main__":
    query = "Saurav's Career Updates"

    pooled = retrieve(query)

    print("--- All candidates ---")
    for h in pooled:
        flag = "PASS" if h["score"] >= MIN_SCORE else "drop"
        print(f"{h['score']:.2f} [{flag}] {h['source']} | {h.get('number')} | {h.get('short_description')}")

    selected = [h for h in pooled if h["score"] >= MIN_SCORE][:FINAL_TOP_K]

    print("\n--- Sent to agent ---")
    if not selected:
        print("(nothing passed the threshold: agent should say it could not find the answer)")
    for h in selected:
        print(f"sys_id: {h.get('sys_id')}")
        print(f"number: {h.get('number')}")
        print(f"short_description: {h.get('short_description')}")
        print(f"description: {h.get('description')}")
        print("-" * 30)
