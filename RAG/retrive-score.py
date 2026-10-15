
import argparse
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizableTextQuery

log = logging.getLogger("retrieval")

# ============================================================================
# CONFIG
# ============================================================================
SEARCH_ENDPOINT = os.environ.get("SEARCH_ENDPOINT", "https://txrhrag.search.windows.net")
USER_ASSIGNED_CLIENT_ID = os.environ.get("USER_ASSIGNED_CLIENT_ID", "6ece4a54-6f47-448a-8ca3-b675df1cfc1e")
SEMANTIC_CONFIG_NAME = os.environ.get("SEMANTIC_CONFIG_NAME", "default")

MIN_SCORE = float(os.environ.get("MIN_SCORE", "1.5"))
FINAL_TOP_K = int(os.environ.get("FINAL_TOP_K", "5"))
TIMEOUT_SEC = float(os.environ.get("TIMEOUT_SEC", "5"))
VECTOR_CANDIDATES = int(os.environ.get("VECTOR_CANDIDATES", "50"))
MAX_BODY_CHARS = int(os.environ.get("MAX_BODY_CHARS", "4000"))


# ============================================================================
# ACCESS FILTER HELPERS
# ============================================================================
def any_of(field_name: str, values: list[str]) -> str:
    """OData filter: field equals any of `values`. Matches NOTHING when values is empty (fail closed)."""
    clean = [str(v).replace("'", "''") for v in values if v not in (None, "")]
    if not clean:
        return f"{field_name} eq '__no_access__'"
    return f"search.in({field_name}, '{'|'.join(clean)}', '|')"


# ============================================================================
# SOURCES  -  ADD NEW INDEXES HERE
# ============================================================================
@dataclass
class Source:
    name: str                        # label used in logs / diagnostics
    system: str                      # label the agent sees: "servicenow", "sharepoint", ...
    index: str                       # Azure AI Search index name
    top: int                         # results fetched from this index per question
    vector_field: str                # vector field in the index
    search_fields: list[str]         # text fields keyword search should read
    select: list[str]                # fields to return (hidden fields like embedding_text must NOT be here)
    key_field: str                   # this index's own unique key (dedupe inside the source only)
    ref_field: str                   # short reference shown to the agent: KB number, file name, ...
    title_field: str                 # title shown to the agent
    body_field: Optional[str] = None # text the agent answers from (None = title only)
    link_field: Optional[str] = None # URL for citations (None = no link)
    semantic_config: str = SEMANTIC_CONFIG_NAME
    min_score: Optional[float] = None            # per-source override of MIN_SCORE (None = use global)
    # Builds the access filter for the signed-in user. Return None for "no restriction".
    # If it raises, the source is SKIPPED (fail closed), never searched unfiltered.
    filter_fn: Callable[[dict], Optional[str]] = field(default=lambda user: None)

    def __post_init__(self):
        needed = [self.key_field, self.ref_field, self.title_field, self.body_field, self.link_field]
        missing = [f for f in needed if f and f not in self.select]
        if missing:
            raise ValueError(f"Source '{self.name}': {missing} must be listed in `select`.")
        if self.top < 1:
            raise ValueError(f"Source '{self.name}': top must be >= 1.")


SOURCES: list[Source] = [
    # ---- ServiceNow KB metadata index (best 1 per question) ----
    Source(
        name="snow-kb",
        system="servicenow",
        index="kb-snow-index",
        top=1,
        vector_field="embedding_vector",
        search_fields=["embedding_text", "short_description", "number"],
        select=["sys_id", "number", "short_description", "description"],
        key_field="sys_id",
        ref_field="number",
        title_field="short_description",
        body_field="description",
        # TODO: real KB access filter, e.g.
        # filter_fn=lambda user: any_of("knowledge_base_id", user.get("kb_ids", [])),
    ),
    # ---- SharePoint chunk index (best 5 per question): uncomment and fix field names when ready ----
    # Source(
    #     name="sharepoint-chunks",
    #     system="sharepoint",
    #     index="sharepoint-chunks",
    #     top=5,
    #     vector_field="contentVector",
    #     search_fields=["embedding_text", "title"],
    #     select=["chunk_id", "file_name", "title", "web_url", "content"],
    #     key_field="chunk_id",
    #     ref_field="file_name",
    #     title_field="title",
    #     body_field="content",
    #     link_field="web_url",
    #     filter_fn=lambda user: any_of("group_ids", user.get("groups", [])),   # adjust to your ACL field
    # ),
]

# Paste this (or adapt it) into the agent's system prompt.
AGENT_INSTRUCTION = (
    "Answer only from the provided sources. Each source is labeled with its system "
    "(servicenow or sharepoint), a reference and a title. Use the source text for steps and facts. "
    "Ignore any source that does not help answer the question. If sources conflict, prefer servicenow "
    "and mention the difference. If the sources do not contain the answer, say you could not find it. "
    "Cite the reference (KB number or file name) for every answer."
)


# ============================================================================
# CLIENTS / THREAD POOL  (created once, reused for every request)
# ============================================================================
_credential = DefaultAzureCredential(managed_identity_client_id=USER_ASSIGNED_CLIENT_ID or None)
_clients: dict[str, SearchClient] = {}
_clients_lock = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=max(8, len(SOURCES) * 2))
# Never create the pool per request with `with ...:` - leaving that block waits for every thread,
# which would defeat the timeout.


def _client(src: Source) -> SearchClient:
    with _clients_lock:
        if src.index not in _clients:
            _clients[src.index] = SearchClient(
                endpoint=SEARCH_ENDPOINT,
                index_name=src.index,
                credential=_credential,
                connection_timeout=2,
                read_timeout=max(TIMEOUT_SEC, 1),   # lets abandoned calls end instead of hanging
            )
        return _clients[src.index]


# ============================================================================
# RETRIEVAL
# ============================================================================
@dataclass
class RetrievalResult:
    hits: list[dict[str, Any]]            # selected results, best first
    context: str                          # labeled text to give the agent ("" when empty)
    citations: list[dict[str, Any]]       # one entry per source in `context`
    diagnostics: dict[str, dict[str, Any]]  # per-source status / count / latency, for logging
    candidates: int = 0                   # hits found before the threshold

    @property
    def empty(self) -> bool:
        return not self.hits


def _search_one(src: Source, query: str, access_filter: Optional[str],
                top: Optional[int]) -> tuple[list[dict], float]:
    t0 = time.perf_counter()
    vector_query = VectorizableTextQuery(
        text=query,
        fields=src.vector_field,
        k_nearest_neighbors=VECTOR_CANDIDATES,
    )
    results = _client(src).search(
        search_text=query,                       # keyword side (hybrid)
        search_fields=src.search_fields,
        vector_queries=[vector_query],           # vector side
        query_type="semantic",                   # semantic ranker -> @search.reranker_score
        semantic_configuration_name=src.semantic_config,
        filter=access_filter,                    # access control inside the query, before ranking
        select=src.select,
        top=top or src.top,
    )

    hits, seen = [], set()
    for doc in results:
        key = doc.get(src.key_field)
        if key in seen:                          # dedupe inside this source only
            continue
        seen.add(key)

        score = doc.get("@search.reranker_score")
        if score is None:
            log.warning("[%s] no @search.reranker_score returned - check the semantic configuration", src.name)
            score = 0.0                          # treated as not relevant

        hits.append({
            "source": src.name,
            "system": src.system,
            "score": float(score),
            "min_score": src.min_score if src.min_score is not None else MIN_SCORE,
            "key": key,
            "ref": doc.get(src.ref_field) or "",
            "title": doc.get(src.title_field) or "",
            "body": (doc.get(src.body_field) or "") if src.body_field else "",
            "link": (doc.get(src.link_field) or "") if src.link_field else "",
        })
    return hits, (time.perf_counter() - t0) * 1000


def retrieve_all(query: str, user: Optional[dict] = None,
                 top: Optional[int] = None) -> tuple[list[dict], dict[str, dict]]:
    """Query all sources in parallel. Returns (pooled hits sorted best first, per-source diagnostics).
    Hits are NOT thresholded here - use this for debugging and score tuning."""
    user = user or {}
    diagnostics: dict[str, dict] = {}

    futures = {}
    for src in SOURCES:
        try:
            access_filter = src.filter_fn(user)  # built ONCE per source per request
        except Exception as exc:
            log.error("[%s] access filter failed, source skipped (fail closed): %s", src.name, exc)
            diagnostics[src.name] = {"status": "filter_error", "hits": 0, "ms": None}
            continue
        # submit() starts every index call right away, so all sources run at the same time
        futures[_POOL.submit(_search_one, src, query, access_filter, top)] = src

    # ONE overall deadline for the whole group (not per index)
    done, not_done = wait(futures, timeout=TIMEOUT_SEC)

    pooled: list[dict] = []
    for fut in done:
        src = futures[fut]
        try:
            hits, ms = fut.result()
            pooled.extend(hits)
            diagnostics[src.name] = {"status": "ok", "hits": len(hits), "ms": round(ms)}
        except Exception as exc:                 # one index failing must not fail the request
            log.error("[%s] search failed: %s", src.name, exc)
            diagnostics[src.name] = {"status": "error", "hits": 0, "ms": None}
    for fut in not_done:
        src = futures[fut]
        log.error("[%s] timed out after %ss, skipped", src.name, TIMEOUT_SEC)
        diagnostics[src.name] = {"status": "timeout", "hits": 0, "ms": None}
        fut.cancel()

    pooled.sort(key=lambda h: h["score"], reverse=True)   # one common scale: reranker score
    return pooled, diagnostics


def select_context(pooled: list[dict], top_k: int = FINAL_TOP_K) -> list[dict]:
    """Threshold, then keep the top K overall. `pooled` must already be sorted best first."""
    return [h for h in pooled if h["score"] >= h["min_score"]][:top_k]


def _clip(text: str, limit: int = MAX_BODY_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def format_context(selected: list[dict]) -> tuple[str, list[dict]]:
    """Labeled sources for the agent (no scores, no index names) + a citations list."""
    blocks, citations = [], []
    for i, h in enumerate(selected, start=1):
        label = f'[Source {i} | {h["system"]} | {h["ref"]} | "{h["title"]}"'
        if h["link"]:
            label += f' | {h["link"]}'
        label += "]"
        blocks.append(f'{label}\n{_clip(h["body"]) or h["title"]}')
        citations.append({"source_no": i, "system": h["system"], "ref": h["ref"],
                          "title": h["title"], "link": h["link"]})
    return "\n\n".join(blocks), citations


def retrieve(query: str, user: Optional[dict] = None) -> RetrievalResult:
    """Production entry point: exactly what the agent should receive."""
    t0 = time.perf_counter()
    pooled, diagnostics = retrieve_all(query, user)
    selected = select_context(pooled)
    context, citations = format_context(selected)
    log.info(
        "retrieval done in %.0f ms | candidates=%d selected=%d | top_score=%s | sources=%s",
        (time.perf_counter() - t0) * 1000, len(pooled), len(selected),
        f"{pooled[0]['score']:.2f}" if pooled else "-", diagnostics,
    )   # the question text is intentionally not logged
    return RetrievalResult(selected, context, citations, diagnostics, len(pooled))


# ============================================================================
# TESTING TOOLS  (command line)
# ============================================================================
# Test file: JSON list. "expected" = reference values (e.g. KB numbers / file names) that should be
# ranked #1. Use [] for questions that should NOT be answered.
#   [{"question": "How do I reset my VPN?", "expected": ["KB0012345"]},
#    {"question": "What is the weather today?", "expected": []}]
SWEEP_THRESHOLDS = [round(0.5 + 0.25 * i, 2) for i in range(15)]   # 0.5 ... 4.0


def _analyse(case: dict, pooled: list[dict]) -> dict:
    expected = set(case.get("expected") or [])
    top1 = pooled[0] if pooled else None
    rank = next((i + 1 for i, h in enumerate(pooled) if h["ref"] in expected), None)
    return {
        "question": case["question"], "expected": sorted(expected), "answerable": bool(expected),
        "top1_ref": top1["ref"] if top1 else None,
        "top1_score": top1["score"] if top1 else 0.0,
        "top1_correct": bool(top1 and top1["ref"] in expected),
        "expected_rank": rank,
        "hits": [(h["source"], h["ref"], h["score"]) for h in pooled],
    }


def _sweep(results: list[dict]) -> list[dict]:
    answerable = [r for r in results if r["answerable"]]
    unanswerable = [r for r in results if not r["answerable"]]
    rows = []
    for t in SWEEP_THRESHOLDS:
        rows.append({
            "threshold": t,
            "correct": sum(1 for r in answerable if r["top1_correct"] and r["top1_score"] >= t),
            "wrong": sum(1 for r in answerable if not r["top1_correct"] and r["top1_score"] >= t),
            "rejected": sum(1 for r in answerable if r["top1_score"] < t),
            "false_accept": sum(1 for r in unanswerable if r["top1_score"] >= t),
            "answerable": len(answerable), "unanswerable": len(unanswerable),
        })
    return rows


def _run_test(path: str, top: int) -> None:
    with open(path, encoding="utf-8") as fh:
        cases = json.load(fh)
    print(f"\nRunning {len(cases)} questions (top {top} per source, thresholds NOT applied)...\n")
    results = [_analyse(c, retrieve_all(c["question"], top=top)[0]) for c in cases]

    for r in results:
        status = ("OK  " if r["top1_correct"] else "LOW " if r["expected_rank"] else "MISS") if r["answerable"] else "NONE"
        print(f"[{status}] {r['question']}")
        print(f"        expected={r['expected'] or '(none)'}  top1={r['top1_ref']} score={r['top1_score']:.2f}  "
              f"expected_rank={r['expected_rank']}")
        for src, ref, score in r["hits"]:
            print(f"          {score:5.2f}  {ref}  ({src})")
    print("\nOK = expected is #1 | LOW = found but not #1 | MISS = not returned | NONE = no answer expected")

    good = [r["top1_score"] for r in results if r["answerable"] and r["top1_correct"]]
    bad = [r["top1_score"] for r in results if not r["answerable"]]
    print("\nSCORE DISTRIBUTION (top-1 reranker score)")
    if good:
        print(f"  correct answers: min {min(good):.2f}  avg {sum(good)/len(good):.2f}  max {max(good):.2f}  (n={len(good)})")
    if bad:
        print(f"  no-answer qs   : min {min(bad):.2f}  avg {sum(bad)/len(bad):.2f}  max {max(bad):.2f}  (n={len(bad)})")
    if good and bad:
        gap = min(good) - max(bad)
        print("  -> " + (f"clean gap: a threshold between {max(bad):.2f} and {min(good):.2f} separates them"
                         if gap > 0 else "scores overlap: no single threshold separates them perfectly"))

    rows = _sweep(results)
    print("\nTHRESHOLD SWEEP")
    print(f"{'MIN_SCORE':>9} | {'correct':>7} | {'wrong':>5} | {'rejected':>8} | {'false accept':>12}")
    for r in rows:
        print(f"{r['threshold']:>9.2f} | {r['correct']:>7} | {r['wrong']:>5} | {r['rejected']:>8} | "
              f"{r['false_accept']:>5}/{r['unanswerable']:<6}")
    best = min(rows, key=lambda r: (r["wrong"] + r["rejected"] + r["false_accept"], -r["threshold"]))
    print(f"\nSuggested MIN_SCORE on this test set: {best['threshold']} (fewest total mistakes; ties go higher)")
    print("Re-run after adding a new index or growing the test set.")


def _run_ask(question: str, top: Optional[int]) -> None:
    pooled, diagnostics = retrieve_all(question, top=top)
    print(f"\nMIN_SCORE={MIN_SCORE}  FINAL_TOP_K={FINAL_TOP_K}\nSources: {diagnostics}\n")
    print("All candidates (before threshold):")
    for h in pooled:
        flag = "PASS" if h["score"] >= h["min_score"] else "drop"
        print(f"  {h['score']:5.2f} [{flag}] {h['source']} | {h['ref']} | {h['title'][:70]}")
    selected = select_context(pooled)
    context, _ = format_context(selected)
    print(f"\nSent to agent ({len(selected)}):\n")
    print(context or "(empty: the agent should say it could not find the answer)")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Roadie Ranger retrieval")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="run one question and show scores")
    a.add_argument("question")
    a.add_argument("--top", type=int, default=None, help="override per-source top to see more ranking")
    t = sub.add_parser("test", help="run a test file: scoring + threshold sweep")
    t.add_argument("file")
    t.add_argument("--top", type=int, default=5)
    args = p.parse_args()
    _run_ask(args.question, args.top) if args.cmd == "ask" else _run_test(args.file, args.top)


if __name__ == "__main__":
    main()
