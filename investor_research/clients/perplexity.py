"""Perplexity Sonar client.

Perplexity exposes an OpenAI-compatible chat completions API at
``https://api.perplexity.ai``. We use the official ``openai`` SDK pointed at
that base URL so we get the same retry/transport ergonomics.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Type, TypeVar

from openai import APIError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from investor_research.config import Settings
from investor_research.models import ResearchEvidence, ResearchResult, Source
from investor_research.prompts.system import SYSTEM_PROMPT
from investor_research.source_quality import enrich_source

logger = logging.getLogger(__name__)

PERPLEXITY_BASE_URL = "https://api.perplexity.ai"

T = TypeVar("T", bound=BaseModel)


class PerplexityError(RuntimeError):
    """Raised when a Perplexity call fails after retries or returns garbage."""


@dataclass
class PerplexityResponse:
    content: str
    sources: list[Source]
    usage: dict[str, int]


class PerplexityClient:
    name = "perplexity"

    def __init__(self, settings: Settings, client: OpenAI | None = None) -> None:
        self._settings = settings
        if client is None and not settings.perplexity_api_key:
            raise PerplexityError("PERPLEXITY_API_KEY is not set.")
        self._client = client or OpenAI(
            api_key=settings.perplexity_api_key,
            base_url=PERPLEXITY_BASE_URL,
            timeout=settings.request_timeout_seconds,
            max_retries=0,
        )

    # ---- provider interface --------------------------------------------

    def search(
        self,
        query: str,
        *,
        recency_days: int | None = None,
        max_results: int = 10,  # noqa: ARG002 - Sonar doesn't expose result cap
    ) -> ResearchEvidence:
        """Provider-interface search via Sonar.

        Issues a single Sonar request, returns the synthesized answer plus
        the citations Sonar gathered while answering. Used in
        ``raw-evidence`` mode (current default) — for schema-enforced
        structured outputs, call ``query_structured`` directly.
        """
        user = query
        if recency_days is not None:
            user = f"Limit to material developments from the last {recency_days} days.\n\n{query}"

        response = self.query(SYSTEM_PROMPT, user)
        results = [
            ResearchResult(title=src.title, url=src.url, content="")
            for src in response.sources
        ]
        return ResearchEvidence(
            provider="perplexity",
            query=query,
            answer=response.content or None,
            results=results,
            sources=response.sources,
            raw_meta={"usage": response.usage},
        )

    # ---- legacy structured API -----------------------------------------

    def query(
        self,
        system: str,
        user: str,
        *,
        schema: Type[BaseModel] | None = None,
    ) -> PerplexityResponse:
        """Make a Sonar request, optionally constrained to a Pydantic schema."""
        response_format: dict[str, Any] | None = None
        if schema is not None:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": _strip_titles(schema.model_json_schema()),
                },
            }

        raw = self._call_with_retry(system, user, response_format)
        content = _extract_content(raw)
        sources = _extract_sources(raw)
        usage = _extract_usage(raw)

        logger.info(
            "perplexity.query.ok",
            extra={
                "model": self._settings.perplexity_model,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "citations_count": len(sources),
            },
        )
        return PerplexityResponse(content=content, sources=sources, usage=usage)

    def query_structured(
        self,
        system: str,
        user: str,
        schema: Type[T],
    ) -> tuple[T, list[Source]]:
        """Like ``query`` but parses the response into ``schema``."""
        response = self.query(system, user, schema=schema)
        try:
            parsed = schema.model_validate_json(response.content)
        except (ValidationError, json.JSONDecodeError) as exc:
            logger.warning(
                "perplexity.query.invalid_json",
                extra={"schema": schema.__name__, "preview": response.content[:200]},
            )
            raise PerplexityError(
                f"Sonar response did not match schema {schema.__name__}: {exc}"
            ) from exc
        return parsed, response.sources

    # ---- internals -----------------------------------------------------

    def _call_with_retry(
        self,
        system: str,
        user: str,
        response_format: dict[str, Any] | None,
    ) -> Any:
        try:
            return _retry_call(self._client, self._settings, system, user, response_format)
        except RetryError as exc:
            cause = exc.last_attempt.exception() if exc.last_attempt else exc
            raise PerplexityError(f"Perplexity request failed: {cause}") from cause


_RETRY_EXCEPTIONS = (RateLimitError, APITimeoutError, APIError)


@retry(
    reraise=False,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
)
def _retry_call(
    client: OpenAI,
    settings: Settings,
    system: str,
    user: str,
    response_format: dict[str, Any] | None,
) -> Any:
    kwargs: dict[str, Any] = {
        "model": settings.perplexity_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if response_format is not None:
        kwargs["response_format"] = response_format
    try:
        return client.chat.completions.create(**kwargs)
    except APIStatusError as exc:
        # 4xx (other than 429) — do not retry.
        if exc.status_code is not None and 400 <= exc.status_code < 500 and exc.status_code != 429:
            raise PerplexityError(f"Perplexity {exc.status_code}: {exc.message}") from exc
        raise


def _extract_content(raw: Any) -> str:
    try:
        choice = raw.choices[0]
        return choice.message.content or ""
    except (AttributeError, IndexError) as exc:
        raise PerplexityError(f"Malformed Perplexity response: {exc}") from exc


def _extract_sources(raw: Any) -> list[Source]:
    """Sonar exposes citations on the response object (``citations`` attr).

    Newer payloads also include a ``search_results`` array with richer
    metadata. We try both, deduplicating by URL.
    """
    seen: dict[str, Source] = {}

    raw_citations = getattr(raw, "citations", None) or []
    for entry in raw_citations:
        if isinstance(entry, str):
            url = entry
            seen.setdefault(url, enrich_source(Source(url=url)))
        elif isinstance(entry, dict):
            url = entry.get("url")
            if url:
                seen.setdefault(
                    url,
                    enrich_source(
                        Source(
                            url=url,
                            title=entry.get("title"),
                            publisher=entry.get("publisher") or entry.get("source"),
                        )
                    ),
                )

    search_results = getattr(raw, "search_results", None) or []
    for entry in search_results:
        if isinstance(entry, dict):
            url = entry.get("url")
            if url and url not in seen:
                seen[url] = enrich_source(
                    Source(
                        url=url,
                        title=entry.get("title"),
                        publisher=entry.get("publisher") or entry.get("source"),
                    )
                )

    return list(seen.values())


def _extract_usage(raw: Any) -> dict[str, int]:
    usage = getattr(raw, "usage", None)
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return {k: v for k, v in usage.model_dump().items() if isinstance(v, int)}
    if isinstance(usage, dict):
        return {k: v for k, v in usage.items() if isinstance(v, int)}
    return {}


def _strip_titles(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively strip Pydantic-generated ``title`` keys.

    Some structured-output backends reject schemas with ``title`` at the
    property level. Strip them defensively.
    """
    if isinstance(schema, dict):
        return {k: _strip_titles(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_strip_titles(item) for item in schema]  # type: ignore[return-value]
    return schema
