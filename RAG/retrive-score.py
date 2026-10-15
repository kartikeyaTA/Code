from concurrent.futures import ThreadPoolExecutor, wait

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizableTextQuery

# ---------------------------------------------------------------------------
# 1. Configuration
# ---------------------------------------------------------------------------
SEARCH_ENDPOINT = "https://txrhrag.search.windows.net"
USER_ASSIGNED_CLIENT_ID = "6ece4a54-6f47-448a-8ca3-b675df1cfc1e"

MIN_SCORE = 1.5      # minimum @search.reranker_score (0-4) to be considered relevant
FINAL_TOP_K = 5      # max results sent to the agent
TIMEOUT_SEC = 5      # one overall deadline for all indexes together

# ---------------------------------------------------------------------------
# 2. Sources: ADD A NEW INDEX BY ADDING ONE ENTRY HERE
#    ref/title/body/link = which index fields the agent should see (link is optional)
# ---------------------------------------------------------------------------
SOURCES = [
    {
        "name": "servicenow",
        "index": "kb-snow-index",
        "top": 1,
        "vector_field": "embedding_vector",
        "search_fields": ["embedding_text", "short_description", "number"],
        "select": ["sys_id", "number", "short_description", "description"],
        "ref": "number",
        "title": "short_description",
        "body": "description",
        "link": None,
        "filter": None,  # TODO: access filter for the signed-in user
    },
    # {
    #     "name": "sharepoint",
    #     "index": "sharepoint-chunks",
    #     "top": 5,
    #     "vector_field": "contentVector",
    #     "search_fields": ["embedding_text", "title"],
    #     "select": ["chunk_id", "file_name", "title", "web_url", "content"],
    #     "ref": "file_name",
    #     "title": "title",
    #     "body": "content",
    #     "link": "web_url",
    #     "filter": None,
    # },
]

# ---------------------------------------------------------------------------
# 3. Setup (created once)
# ---------------------------------------------------------------------------
credential = DefaultAzureCredential(managed_identity_client_id=USER_ASSIGNED_CLIENT_ID)
POOL = ThreadPoolExecutor(max_workers=8)  # shared pool; do not create per request


# ---------------------------------------------------------------------------
# 4. Search one index (hybrid: keyword + vector, semantic ranker on)
# ---------------------------------------------------------------------------
def search_one(src, query):
    client = SearchClient(SEARCH_ENDPOINT, src["index"], credential)

    results = client.search(
        search_text=query,
        search_fields=src["search_fields"],
        vector_queries=[VectorizableTextQuery(text=query, fields=src["vector_field"], k_nearest_neighbors=50)],
        query_type="semantic",
        semantic_configuration_name="default",
        filter=src["filter"],
        select=src["select"],
        top=src["top"],
    )

    hits = []
    for doc in results:
        score = doc.get("@search.reranker_score")
        if score is None:
            print(f"[{src['name']}] WARNING: no reranker score - check the semantic configuration")
            score = 0.0
        hits.append({
            "source": src["name"],
            "score": score,
            "ref": doc.get(src["ref"]) or "",
            "title": doc.get(src["title"]) or "",
            "body": doc.get(src["body"]) or "",
            "link": (doc.get(src["link"]) or "") if src["link"] else "",
        })
    return hits


# ---------------------------------------------------------------------------
# 5. Search all indexes in parallel, rank together, apply threshold
# ---------------------------------------------------------------------------
def retrieve(query):
    futures = {POOL.submit(search_one, src, query): src for src in SOURCES}
    done, not_done = wait(futures, timeout=TIMEOUT_SEC)

    pooled = []
    for fut in done:
        try:
            pooled.extend(fut.result())
        except Exception as exc:  # one failing index must not fail the request
            print(f"[{futures[fut]['name']}] failed: {exc}")
    for fut in not_done:
        print(f"[{futures[fut]['name']}] timed out, skipped")

    pooled.sort(key=lambda h: h["score"], reverse=True)  # one common scale
    return [h for h in pooled if h["score"] >= MIN_SCORE][:FINAL_TOP_K]


# ---------------------------------------------------------------------------
# 6. Format what the agent receives (no scores, no index names)
# ---------------------------------------------------------------------------
def format_context(hits):
    blocks = []
    for i, h in enumerate(hits, start=1):
        label = f'[Source {i} | {h["source"]} | {h["ref"]} | "{h["title"]}"'
        label += f' | {h["link"]}]' if h["link"] else "]"
        blocks.append(f'{label}\n{h["body"] or h["title"]}')
    return "\n\n".join(blocks)


if __name__ == "__main__":
    query = "Saurav's Career Updates"   # <- change this

    hits = retrieve(query)

    print(f"--- Sent to agent ({len(hits)}) ---")
    for h in hits:
        print(f"{h['score']:.2f} | {h['source']} | {h['ref']} | {h['title']}")
    print()
    print(format_context(hits) or "(nothing passed the threshold: agent should say it could not find the answer)")
