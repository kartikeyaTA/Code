"""
ServiceNow Knowledge Base tool for a helpdesk agent.

Wraps the sn_km_api Knowledge Management REST API to:
  1. Search knowledge articles relevant to a user query
  2. Fetch full article content for the top matches
  3. Return a single, agent-ready payload (search + fetch combined)

Auth: supports either OAuth2 (client_credentials) or Basic Auth.
Set the relevant environment variables before use (see bottom of file).

Usage as an agent tool:
    from servicenow_kb_tool import get_relevant_knowledge_articles

    result = get_relevant_knowledge_articles("how do I reset my VPN password")
    # -> list[dict] with sys_id, number, title, url, content

If you're wiring this into a framework that expects a JSON-schema tool
definition (OpenAI/Anthropic function calling), see TOOL_SCHEMA at the
bottom — pass that as the tool spec and call
get_relevant_knowledge_articles(**args) when the model invokes it.
"""

import os
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("servicenow_kb_tool")


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

@dataclass
class ServiceNowConfig:
    instance_url: str = field(default_factory=lambda: os.environ.get("SN_INSTANCE_URL", ""))
    # OAuth2 (preferred)
    client_id: Optional[str] = field(default_factory=lambda: os.environ.get("SN_CLIENT_ID"))
    client_secret: Optional[str] = field(default_factory=lambda: os.environ.get("SN_CLIENT_SECRET"))
    oauth_username: Optional[str] = field(default_factory=lambda: os.environ.get("SN_OAUTH_USERNAME"))
    oauth_password: Optional[str] = field(default_factory=lambda: os.environ.get("SN_OAUTH_PASSWORD"))
    # Basic auth (fallback)
    basic_username: Optional[str] = field(default_factory=lambda: os.environ.get("SN_USERNAME"))
    basic_password: Optional[str] = field(default_factory=lambda: os.environ.get("SN_PASSWORD"))

    def __post_init__(self):
        if not self.instance_url:
            raise ValueError("SN_INSTANCE_URL is not set (e.g. https://yourinstance.service-now.com)")
        self.instance_url = self.instance_url.rstrip("/")


_config = None


def get_config() -> ServiceNowConfig:
    global _config
    if _config is None:
        _config = ServiceNowConfig()
    return _config


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

_token_cache = {"access_token": None, "expires_at": 0}


def _get_oauth_token(cfg: ServiceNowConfig) -> str:
    """Fetch (and cache) an OAuth2 access token using the resource owner
    password grant, which is what ServiceNow's inbound OAuth commonly uses
    for service accounts. Swap to 'client_credentials' grant if your
    instance is configured for it."""
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 30:
        return _token_cache["access_token"]

    token_url = f"{cfg.instance_url}/oauth_token.do"
    payload = {
        "grant_type": "password",
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "username": cfg.oauth_username,
        "password": cfg.oauth_password,
    }
    resp = requests.post(token_url, data=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + int(data.get("expires_in", 1800))
    return _token_cache["access_token"]


def _get_auth_headers(cfg: ServiceNowConfig) -> dict:
    if cfg.client_id and cfg.client_secret and cfg.oauth_username:
        token = _get_oauth_token(cfg)
        return {"Authorization": f"Bearer {token}"}
    elif cfg.basic_username and cfg.basic_password:
        # requests handles basic auth separately via `auth=`, but we return
        # headers=None here and let callers use HTTPBasicAuth instead.
        return {}
    else:
        raise ValueError(
            "No usable ServiceNow credentials found. Set either "
            "SN_CLIENT_ID/SN_CLIENT_SECRET/SN_OAUTH_USERNAME/SN_OAUTH_PASSWORD "
            "for OAuth2, or SN_USERNAME/SN_PASSWORD for basic auth."
        )


def _get_request_auth(cfg: ServiceNowConfig):
    """Returns the `auth=` tuple for requests.get() when using basic auth,
    or None if using bearer token auth (already in headers)."""
    if cfg.client_id and cfg.client_secret and cfg.oauth_username:
        return None
    return (cfg.basic_username, cfg.basic_password)


# --------------------------------------------------------------------------
# Core API calls
# --------------------------------------------------------------------------

def search_knowledge_articles(
    query: str,
    kb_ids: Optional[list[str]] = None,
    limit: int = 5,
    timeout: int = 15,
) -> list[dict]:
    """
    Search ServiceNow knowledge articles relevant to a free-text query.

    Args:
        query: The user's question / search text.
        kb_ids: Optional list of knowledge base sys_ids to restrict the
                 search to. Leave empty to search all KBs the service
                 account can see.
        limit: Max number of results to return.

    Returns:
        List of dicts: [{sys_id, number, title, snippet}, ...]
        (search results do not include full article body — use
        get_article_content() or get_relevant_knowledge_articles() for that)
    """
    cfg = get_config()
    url = f"{cfg.instance_url}/api/sn_km_api/knowledge/articles"

    params = {
        "text": query,
        "limit": limit,
        "fields": "sys_id,number,short_description",
    }
    if kb_ids:
        params["kb"] = ",".join(kb_ids)

    headers = _get_auth_headers(cfg)
    auth = _get_request_auth(cfg)

    resp = requests.get(url, headers=headers, auth=auth, params=params, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()

    results = body.get("result", {}).get("results", []) or body.get("result", [])
    articles = []
    for item in results:
        articles.append({
            "sys_id": item.get("sys_id") or item.get("id"),
            "number": item.get("number"),
            "title": item.get("short_description") or item.get("title"),
            "snippet": item.get("snippet", ""),
        })
    return articles


def get_article_content(article_id: str, timeout: int = 15) -> Optional[dict]:
    """
    Fetch the full content of a single knowledge article.

    Args:
        article_id: sys_id or article number (e.g. "KB0012345").

    Returns:
        dict with sys_id, number, title, content, url — or None if not found.
    """
    cfg = get_config()
    url = f"{cfg.instance_url}/api/sn_km_api/knowledge/articles/{article_id}"

    headers = _get_auth_headers(cfg)
    auth = _get_request_auth(cfg)

    resp = requests.get(url, headers=headers, auth=auth, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    result = resp.json().get("result", {})

    content = result.get("content")
    # Templated articles return content as a list of {label, value} fields
    # instead of a single string — flatten it if so.
    if isinstance(content, list):
        content = "\n\n".join(
            f"{field_.get('label', '')}: {field_.get('value', '')}"
            for field_ in content
        )

    return {
        "sys_id": result.get("sys_id"),
        "number": result.get("number"),
        "title": result.get("short_description") or result.get("title"),
        "content": content or "",
        "url": f"{cfg.instance_url}/kb_view.do?sys_kb_id={result.get('sys_id')}",
    }


def get_relevant_knowledge_articles(
    query: str,
    kb_ids: Optional[list[str]] = None,
    top_n: int = 3,
) -> list[dict]:
    """
    Agent-facing tool function: search, then fetch full content for the
    top N results in one call. This is the function you register as the
    agent's tool.

    Args:
        query: The user's question.
        kb_ids: Optional list of knowledge base sys_ids to restrict to.
        top_n: How many full articles to return (keep low — 2-3 is usually
               enough context for grounding an answer without bloating
               the prompt).

    Returns:
        List of dicts: [{sys_id, number, title, content, url}, ...]
    """
    try:
        candidates = search_knowledge_articles(query, kb_ids=kb_ids, limit=top_n)
    except requests.HTTPError as e:
        logger.error(f"ServiceNow search failed: {e}")
        return []

    articles = []
    for c in candidates:
        try:
            full = get_article_content(c["sys_id"])
            if full:
                articles.append(full)
        except requests.HTTPError as e:
            logger.warning(f"Failed to fetch article {c['sys_id']}: {e}")

    return articles


# --------------------------------------------------------------------------
# Tool schema (for function-calling frameworks, e.g. Anthropic/OpenAI)
# --------------------------------------------------------------------------

TOOL_SCHEMA = {
    "name": "get_relevant_knowledge_articles",
    "description": (
        "Search the ServiceNow knowledge base for articles relevant to a "
        "user's helpdesk question, and return the full content of the top "
        "matches so the agent can ground its answer in them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The user's question or issue description.",
            },
            "top_n": {
                "type": "integer",
                "description": "Number of articles to retrieve (default 3).",
                "default": 3,
            },
        },
        "required": ["query"],
    },
}


if __name__ == "__main__":
    # Quick manual test:
    #   SN_INSTANCE_URL=https://yourinstance.service-now.com \
    #   SN_USERNAME=svc_account SN_PASSWORD=*** python servicenow_kb_tool.py
    import json
    results = get_relevant_knowledge_articles("how do I reset my VPN password")
    print(json.dumps(results, indent=2))
