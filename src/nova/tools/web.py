"""Web tools for Nova — search and fetch via local SearXNG.

SearXNG must be running locally (default http://localhost:8888).
"""

import html
import re
from typing import Any

import httpx

from nova.tools.tool import RiskLevel, Tool

SEARXNG_URL = "http://localhost:8888"
DEFAULT_TIMEOUT = 15.0
MAX_FETCH_BYTES = 200_000
USER_AGENT = "Nova/0.1 (+local assistant)"


class WebToolError(Exception):
    """Raised when a web tool cannot complete its request."""


def _strip_html(raw: str) -> str:
    """Crudely strip HTML tags and scripts from page text."""
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    return re.sub(r"[ \t]+", " ", raw)


async def _search(query: str, num_results: int = 5) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                f"{SEARXNG_URL}/search",
                params={"q": query, "format": "json"},
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError as e:
        raise WebToolError(
            f"SearXNG not reachable at {SEARXNG_URL} — is it running?"
        ) from e
    except httpx.HTTPStatusError as e:
        raise WebToolError(f"SearXNG returned HTTP {e.response.status_code}") from e
    except httpx.HTTPError as e:
        raise WebToolError(f"SearXNG request failed: {e}") from e

    results = []
    for r in data.get("results", [])[:num_results]:
        results.append(
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
            }
        )
    return {"query": query, "results": results}


async def _fetch(url: str, max_chars: int = 8000) -> dict[str, Any]:
    if not url.startswith(("http://", "https://")):
        raise WebToolError(f"Refusing to fetch non-http(s) URL: {url}")
    try:
        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(
                url, headers={"User-Agent": USER_AGENT}
            )
            resp.raise_for_status()
            raw = resp.text[:MAX_FETCH_BYTES]
    except httpx.HTTPStatusError as e:
        raise WebToolError(f"Fetch returned HTTP {e.response.status_code}") from e
    except httpx.HTTPError as e:
        raise WebToolError(f"Fetch failed: {e}") from e

    text = _strip_html(raw)
    return {"url": url, "content": text[:max_chars], "truncated": len(text) > max_chars}


_TOOLS: list[Tool] = [
    Tool(
        name="web.search",
        description="Search the web using local SearXNG. Returns titles, URLs and snippets.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "num_results": {
                    "type": "integer",
                    "description": "Number of results to return (default 5)",
                },
            },
            "required": ["query"],
        },
        risk=RiskLevel.READ,
        permission="web.search",
        implementation=_search,
    ),
    Tool(
        name="web.fetch",
        description="Fetch the content of a web page. Returns plain text, truncated.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http(s) URL to fetch"},
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return (default 8000)",
                },
            },
            "required": ["url"],
        },
        risk=RiskLevel.READ,
        permission="web.fetch",
        implementation=_fetch,
    ),
]


def get_tools() -> list[Tool]:
    return _TOOLS