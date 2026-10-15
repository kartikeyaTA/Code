
import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from azure.core.credentials import AzureKeyCredential
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizableTextQuery, VectorizedQuery

log = logging.getLogger("retrieval")

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
SEARCH_ENDPOINT = os.environ.get("SEARCH_ENDPOINT", "https://txrhrag.search.windows.net")
SEMANTIC_CONFIG_NAME = os.environ.get("SEMANTIC_CONFIG_NAME", "default")
SEARCH_API_KEY = os.environ.get("SEARCH_API_KEY", "")

EMBED_MODE = os.environ.get("EMBED_MODE", "service").lower()   # "service" | "client"
APIM_ENDPOINT = os.environ.get("APIM_ENDPOINT", "")
EMBEDDING_DEPLOYMENT_NAME = os.environ.get("EMBEDDING_DEPLOYMENT_NAME", "text-embedding-3-small")
APIM_SUBSCRIPTION_KEY = os.environ.get("APIM_SUBSCRIPTION_KEY", "")
EMBEDDING_API_VERSION = os.environ.get("EMBEDDING_API_VERSION", "2024-02-01")

MIN_SCORE = float(os.environ.get("MIN_SCORE", "1.5"))
FINAL_TOP_K = int(os.environ.get("FINAL_TOP_K", "5"))
PER_SOURCE_TIMEOUT_SEC = float(os.environ.get("PER_SOURCE_TIMEOUT_SEC", "5"))
VECTOR_CANDIDATES = 50     # k_nearest_neighbors: vector candidates before ranking


@dataclass
class Source:
    name: str                       # label used in logs / output
    kind: str                       # "metadata" or "chunks" (drives formatting)
    index: str
    top: int                        # results fetched per question (1 for metadata, 5 for chunks)
    vector_field: str
    key_field: str                  # this index's own unique key (dedupe within the source only)
    match_field: str                # field compared against "expected" in the test file
    select: list[str]
    search_fields: list[str]        # text fields BM25 should read
    semantic_config: str = SEMANTIC_CONFIG_NAME
    filter_fn: Callable[[dict], Optional[str]] = field(default=lambda user: None)


# ---- ONE source today. Add the SharePoint chunk index here later. ----
SOURCES: list[Source] = [
    Source(
        name="snow-kb",
        kind="metadata",
        index=os.environ.get("SNOW_INDEX_NAME", "kb-snow-index"),
        top=1,
        vector_field="embedding_vector",
        key_field="sys_id",
        match_field="kb_number",
        # embedding_text is hidden (not retrievable), so it is NOT in select
        select=["sys_id", "kb_number", "title", "knowledge_base_name"],
        search_fields=["embedding_text", "title", "kb_number"],
        # TODO: replace with the real KB access / user-criteria filter, e.g.
        # filter_fn=lambda user: "search.in(knowledge_base_id, '{}', ',')".format(",".join(user["kb_ids"]))
    ),
    # Source(
    #     name="sharepoint-chunks",
    #     kind="chunks",
    #     index="sharepoint-chunks",
    #     top=5,
    #     vector_field="contentVector",
    #     key_field="chunk_id",
    #     match_field="file_name",
    #     select=["chunk_id", "file_name", "web_url", "content"],
    #     search_fields=["embedding_text", "title"],
    # ),
]


# ----------------------------------------------------------------------------
# EMBEDDING (only used when EMBED_MODE=client)
# ----------------------------------------------------------------------------
def _embed_sync(text: str) -> list[float]:
    import requests  # local import: only needed in client mode

    if not (APIM_ENDPOINT and APIM_SUBSCRIPTION_KEY):
        raise RuntimeError("EMBED_MODE=client needs APIM_ENDPOINT and APIM_SUBSCRIPTION_KEY.")
    # Mirrors what the indexer's embedding skill calls: {resource_url}/openai/deployments/{name}/embeddings
    url = (
        f"{APIM_ENDPOINT.rstrip('/')}/openai/deployments/{EMBEDDING_DEPLOYMENT_NAME}"
        f"/embeddings?api-version={EMBEDDING_API_VERSION}"
    )
    resp = requests.post(
        url,
        headers={"api-key": APIM_SUBSCRIPTION_KEY, "Content-Type": "application/json"},
        json={"input": text},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


async def embed_query(text: str) -> list[float]:
    return await asyncio.to_thread(_embed_sync, text)


# ----------------------------------------------------------------------------
# RETRIEVAL
# ----------------------------------------------------------------------------
def _make_credential():
    return AzureKeyCredential(SEARCH_API_KEY) if SEARCH_API_KEY else DefaultAzureCredential()


async def _search_one(cred, src: Source, query: str, vector: Optional[list[float]],
                      user: dict, top: Optional[int]) -> list[dict[str, Any]]:
    if vector is not None:
        vq = VectorizedQuery(vector=vector, k_nearest_neighbors=VECTOR_CANDIDATES, fields=src.vector_field)
    else:  # service-side vectorization: the index's vectorizer embeds the query text
        vq = VectorizableTextQuery(text=query, k_nearest_neighbors=VECTOR_CANDIDATES, fields=src.vector_field)

    t0 = time.perf_counter()
    async with SearchClient(SEARCH_ENDPOINT, src.index, cred) as client:
        results = await client.search(
            search_text=query,                       # keyword side (reads embedding_text)
            search_fields=src.search_fields,
            vector_queries=[vq],                     # vector side
            query_type="semantic",
            semantic_configuration_name=src.semantic_config,
            filter=src.filter_fn(user),
            select=src.select,
            top=top or src.top,
        )
        hits, seen = [], set()
        async for r in results:
            key = r.get(src.key_field)
            if key in seen:                          # dedupe within this source only
                continue
            seen.add(key)
            score = r.get("@search.reranker_score")
            if score is None:
                log.warning("[%s] no @search.reranker_score returned - check the semantic configuration", src.name)
                score = 0.0
            hits.append({
                **{k: v for k, v in r.items() if not k.startswith("@")},
                "kind": src.kind,
                "source": src.name,
                "match_value": r.get(src.match_field),
                "score": float(score),
                "rrf_score": r.get("@search.score"),
            })
    log.info("[%s] %d hits in %.0f ms", src.name, len(hits), (time.perf_counter() - t0) * 1000)
    return hits


async def _guarded(cred, src, query, vector, user, top):
    """One source failing or timing out must not fail the whole request."""
    try:
        return await asyncio.wait_for(
            _search_one(cred, src, query, vector, user, top), timeout=PER_SOURCE_TIMEOUT_SEC
        )
    except Exception as exc:
        log.error("[%s] failed: %s", src.name, exc)
        return []


def select_context(hits: list[dict[str, Any]], min_score: float = MIN_SCORE,
                   top_k: int = FINAL_TOP_K) -> list[dict[str, Any]]:
    """Pool is already merged. Threshold, sort on the one common scale, keep top K."""
    relevant = [h for h in hits if h["score"] >= min_score]
    relevant.sort(key=lambda h: h["score"], reverse=True)
    return relevant[:top_k]


async def retrieve_all(query: str, user: Optional[dict] = None,
                       top: Optional[int] = None) -> list[dict[str, Any]]:
    """Run all sources in parallel and return the pooled, UNFILTERED, ranked hits (for scoring/debug)."""
    user = user or {}
    vector = await embed_query(query) if EMBED_MODE == "client" else None   # embed once, reuse everywhere
    cred = _make_credential()
    try:
        per_source = await asyncio.gather(*[_guarded(cred, s, query, vector, user, top) for s in SOURCES])
    finally:
        await cred.close()
    pooled = [h for hits in per_source for h in hits]
    pooled.sort(key=lambda h: h["score"], reverse=True)
    return pooled


async def retrieve(query: str, user: Optional[dict] = None) -> list[dict[str, Any]]:
    """Production entry point: what the agent should get."""
    return select_context(await retrieve_all(query, user))


def format_context(selected: list[dict[str, Any]]) -> str:
    if not selected:
        return ""
    blocks = []
    for i, h in enumerate(selected, start=1):
        if h["kind"] == "metadata":
            blocks.append(
                f'[Source {i} | servicenow | {h.get("kb_number", "")} | "{h.get("title", "")}"]\n'
                f'KB: {h.get("knowledge_base_name", "")}. Title: {h.get("title", "")}'
            )
        else:
            blocks.append(
                f'[Source {i} | sharepoint | "{h.get("file_name", "")}" | {h.get("web_url", "")}]\n'
                f'{h.get("content", "")}'
            )
    return "\n\n".join(blocks)


# ----------------------------------------------------------------------------
# SCORING TEST HARNESS
# ----------------------------------------------------------------------------
# Test file format (JSON list). "expected" = values of each source's match_field
# (kb_number for ServiceNow). Use [] for questions that should NOT be answerable.
#   [
#     {"question": "How do I reset my VPN?", "expected": ["KB0012345"]},
#     {"question": "What is the weather today?", "expected": []}
#   ]
SWEEP_THRESHOLDS = [round(0.5 + 0.25 * i, 2) for i in range(0, 15)]   # 0.5 ... 4.0


def analyse(case: dict, hits: list[dict[str, Any]]) -> dict[str, Any]:
    expected = set(case.get("expected") or [])
    ranked_values = [h["match_value"] for h in hits]
    top1 = hits[0] if hits else None
    best_expected_rank = next((i + 1 for i, v in enumerate(ranked_values) if v in expected), None)
    best_expected_score = next((h["score"] for h in hits if h["match_value"] in expected), None)
    return {
        "question": case["question"],
        "expected": sorted(expected),
        "answerable": bool(expected),
        "top1_value": top1["match_value"] if top1 else None,
        "top1_score": top1["score"] if top1 else 0.0,
        "top1_correct": bool(top1 and top1["match_value"] in expected),
        "expected_rank": best_expected_rank,
        "expected_score": best_expected_score,
        "hits": [(h["source"], h["match_value"], h["score"]) for h in hits],
    }


def sweep(results: list[dict[str, Any]], thresholds: list[float]) -> list[dict[str, Any]]:
    """For each MIN_SCORE: how many answerable questions are served correctly / wrongly / rejected,
    and how many unanswerable questions wrongly get an answer."""
    answerable = [r for r in results if r["answerable"]]
    unanswerable = [r for r in results if not r["answerable"]]
    rows = []
    for t in thresholds:
        correct = sum(1 for r in answerable if r["top1_correct"] and r["top1_score"] >= t)
        wrong = sum(1 for r in answerable if not r["top1_correct"] and r["top1_score"] >= t)
        rejected = sum(1 for r in answerable if r["top1_score"] < t)
        false_accept = sum(1 for r in unanswerable if r["top1_score"] >= t)
        rows.append({
            "threshold": t,
            "correct": correct,
            "wrong_source": wrong,
            "rejected": rejected,
            "answerable": len(answerable),
            "correct_pct": (100 * correct / len(answerable)) if answerable else 0.0,
            "false_accept": false_accept,
            "unanswerable": len(unanswerable),
        })
    return rows


def recommend(rows: list[dict[str, Any]]) -> Optional[float]:
    """Pick the threshold with the fewest total mistakes (wrong source + rejected + false accepts).
    Ties go to the higher threshold (safer). Returns None if there are no rows."""
    if not rows:
        return None
    best = min(rows, key=lambda r: (r["wrong_source"] + r["rejected"] + r["false_accept"], -r["threshold"]))
    return best["threshold"]


async def run_tests(path: str, top: int) -> None:
    with open(path, encoding="utf-8") as fh:
        cases = json.load(fh)

    results = []
    print(f"\nRunning {len(cases)} questions (fetching top {top} per source)...\n")
    for case in cases:
        hits = await retrieve_all(case["question"], top=top)
        results.append(analyse(case, hits))

    # ---- per-question detail ----
    print("=" * 100)
    print("PER QUESTION")
    print("=" * 100)
    for r in results:
        if r["answerable"]:
            status = "OK  " if r["top1_correct"] else ("LOW " if r["expected_rank"] else "MISS")
        else:
            status = "NONE"
        print(f"[{status}] {r['question']}")
        print(f"        expected={r['expected'] or '(none - should not be answered)'}  "
              f"top1={r['top1_value']} score={r['top1_score']:.2f}  "
              f"expected_rank={r['expected_rank']} expected_score="
              f"{'-' if r['expected_score'] is None else format(r['expected_score'], '.2f')}")
        for src, val, score in r["hits"]:
            print(f"          {score:5.2f}  {val}  ({src})")
    print("\nLegend: OK = expected result ranked #1 | LOW = found but not #1 | "
          "MISS = not returned | NONE = no answer expected")

    # ---- score distribution ----
    good = [r["top1_score"] for r in results if r["answerable"] and r["top1_correct"]]
    bad = [r["top1_score"] for r in results if not r["answerable"]]
    print("\n" + "=" * 100)
    print("SCORE DISTRIBUTION (top-1 reranker score)")
    print("=" * 100)
    if good:
        print(f"  correct answers : min {min(good):.2f}  avg {sum(good)/len(good):.2f}  max {max(good):.2f}  (n={len(good)})")
    if bad:
        print(f"  no-answer qs    : min {min(bad):.2f}  avg {sum(bad)/len(bad):.2f}  max {max(bad):.2f}  (n={len(bad)})")
    if good and bad:
        gap = min(good) - max(bad)
        print("  -> " + (f"clean gap of {gap:.2f}: a threshold between {max(bad):.2f} and {min(good):.2f} separates them"
                         if gap > 0 else "scores overlap: no single threshold separates them perfectly"))

    # ---- threshold sweep ----
    rows = sweep(results, SWEEP_THRESHOLDS)
    print("\n" + "=" * 100)
    print("THRESHOLD SWEEP (MIN_SCORE)")
    print("=" * 100)
    print(f"{'MIN_SCORE':>9} | {'correct':>7} | {'wrong src':>9} | {'rejected':>8} | {'correct %':>9} | {'false accept':>12}")
    print("-" * 70)
    for r in rows:
        print(f"{r['threshold']:>9.2f} | {r['correct']:>7} | {r['wrong_source']:>9} | {r['rejected']:>8} | "
              f"{r['correct_pct']:>8.0f}% | {r['false_accept']:>5}/{r['unanswerable']:<6}")
    rec = recommend(rows)
    if rec is not None:
        print(f"\nSuggested MIN_SCORE on this test set: {rec}  (fewest total mistakes; ties go higher)")
    print("Re-check this after you add the SharePoint index and grow the test set.")


async def run_ask(question: str, top: Optional[int]) -> None:
    pooled = await retrieve_all(question, top=top)
    print(f"\nQuestion: {question}\nMIN_SCORE={MIN_SCORE}  FINAL_TOP_K={FINAL_TOP_K}  EMBED_MODE={EMBED_MODE}\n")
    print("All candidates (before threshold):")
    for h in pooled:
        flag = "PASS" if h["score"] >= MIN_SCORE else "drop"
        print(f"  {h['score']:5.2f}  [{flag}]  {h['source']}  {h['match_value']}  {h.get('title', '')[:70]}")
    selected = select_context(pooled)
    print(f"\nSent to agent ({len(selected)}):\n")
    print(format_context(selected) or "(empty context - agent should say it could not find the answer)")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Roadie Ranger retrieval + scoring test")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="run one question and show scores")
    a.add_argument("question")
    a.add_argument("--top", type=int, default=None, help="override per-source top (e.g. 5 to see ranking)")
    t = sub.add_parser("test", help="run a test file and print scoring + threshold sweep")
    t.add_argument("file")
    t.add_argument("--top", type=int, default=5, help="per-source top to fetch during tests (default 5)")
    args = p.parse_args()

    if args.cmd == "ask":
        asyncio.run(run_ask(args.question, args.top))
    else:
        asyncio.run(run_tests(args.file, args.top))


if __name__ == "__main__":
    main()
