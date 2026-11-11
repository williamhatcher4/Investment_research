"""Tavily search client.

Tavily exposes a search-with-citations API at https://api.tavily.com.
Configure ``include_answer="advanced"`` so each call returns both a
synthesized answer string AND the raw search results — this gives Claude
Desktop both a high-level summary and the underlying snippets to cite.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from investor_research.config import Settings
from investor_research.models import ResearchEvidence, ResearchResult, Source
from investor_research.source_quality import enrich_source

logger = logging.getLogger(__name__)


class TavilyError(RuntimeError):
    """Raised when a Tavily call fails after retries."""


class _TavilySDKClient(Protocol):
    def search(self, query: str, **kwargs: Any) -> dict[str, Any]: ...


class TavilyClient:
    name = "tavily"

    def __init__(self, settings: Settings, client: _TavilySDKClient | None = None) -> None:
        self._settings = settings
        self._client = client or _build_default_client(settings)

    def search(
        self,
        query: str,
        *,
        recency_days: int | None = None,
        max_results: int = 10,
    ) -> ResearchEvidence:
        kwargs: dict[str, Any] = {
            "search_depth": self._settings.tavily_search_depth,
            "include_answer": "advanced",
            "max_results": max_results,
            "include_raw_content": False,
        }
        if recency_days is not None:
            kwargs["days"] = recency_days
            kwargs["topic"] = "news"

        try:
            raw = _retry_search(self._client, query, kwargs)
        except RetryError as exc:
            cause = exc.last_attempt.exception() if exc.last_attempt else exc
            raise TavilyError(f"Tavily search failed: {cause}") from cause

        return _to_evidence(query, raw)


# ---- internals ---------------------------------------------------------


_RETRYABLE = (TimeoutError, ConnectionError)


@retry(
    reraise=False,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(_RETRYABLE),
)
def _retry_search(client: _TavilySDKClient, query: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    return client.search(query, **kwargs)


def _build_default_client(settings: Settings) -> _TavilySDKClient:
    if not settings.tavily_api_key:
        raise TavilyError("TAVILY_API_KEY is not set.")
    # Imported lazily so test mode (which always injects a fake) doesn't
    # require the package to be installed.
    from tavily import TavilyClient as _SDK

    return _SDK(api_key=settings.tavily_api_key)


def _to_evidence(query: str, raw: dict[str, Any]) -> ResearchEvidence:
    results_raw = raw.get("results") or []
    results: list[ResearchResult] = []
    sources: list[Source] = []
    seen_urls: set[str] = set()
    for entry in results_raw:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if not url:
            continue
        results.append(
            ResearchResult(
                title=entry.get("title"),
                url=url,
                content=entry.get("content", "") or "",
                score=entry.get("score"),
                published_date=entry.get("published_date"),
            )
        )
        if url not in seen_urls:
            sources.append(
                enrich_source(
                    Source(
                        url=url,
                        title=entry.get("title"),
                        publisher=_publisher_from_url(url),
                    )
                )
            )
            seen_urls.add(url)

    answer = raw.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        answer = None

    logger.info(
        "tavily.search.ok",
        extra={
            "query_preview": query[:80],
            "results_count": len(results),
            "has_answer": answer is not None,
        },
    )

    return ResearchEvidence(
        provider="tavily",
        query=query,
        answer=answer,
        results=results,
        sources=sources,
        raw_meta={"response_time": raw.get("response_time")},
    )


def _publisher_from_url(url: str) -> str | None:
    try:
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
        return host.lstrip("www.") or None
    except Exception:  # noqa: BLE001
        return None
