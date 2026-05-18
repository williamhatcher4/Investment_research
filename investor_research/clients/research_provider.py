"""Provider abstraction over Tavily / Perplexity.

Each tool calls ``provider.search(query, ...)`` and receives a
``ResearchEvidence`` bundle. The active provider is chosen by
``settings.research_provider`` so the user can flip ``RESEARCH_PROVIDER``
in ``.env`` without touching code.
"""

from __future__ import annotations

from typing import Protocol

from investor_research.config import ConfigurationError, Settings
from investor_research.models import ResearchEvidence


class ResearchProvider(Protocol):
    """Minimal interface every provider implements."""

    name: str

    def search(
        self,
        query: str,
        *,
        recency_days: int | None = None,
        max_results: int = 10,
    ) -> ResearchEvidence: ...


def make_research_provider(settings: Settings) -> ResearchProvider:
    """Build the provider selected by ``settings.research_provider``."""
    if settings.research_provider == "tavily":
        # Local import: avoids importing the Perplexity (OpenAI SDK) deps
        # when only Tavily is configured, and vice versa.
        from investor_research.clients.tavily import TavilyClient

        return TavilyClient(settings)
    if settings.research_provider == "perplexity":
        from investor_research.clients.perplexity import PerplexityClient

        return PerplexityClient(settings)
    raise ConfigurationError(
        f"Unknown research provider: {settings.research_provider!r}"
    )
