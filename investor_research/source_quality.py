"""Source-quality classification and lightweight evidence ledgers.

The research tools intentionally return raw evidence rather than final
analysis. This module adds deterministic metadata that helps downstream
models and human reviewers understand which URLs are primary, reputable, or
low-signal before using them in an investment memo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from investor_research.models import ResearchResult, Source


@dataclass(frozen=True)
class SourceQuality:
    source_type: str
    quality_tier: str
    quality_score: float
    quality_notes: str


PRIMARY_DOMAINS = (
    "sec.gov",
    "investor.",
    "ir.",
    "annualreports.com",
)
REPUTABLE_FINANCIAL_DOMAINS = (
    "reuters.com",
    "bloomberg.com",
    "wsj.com",
    "ft.com",
    "cnbc.com",
    "marketwatch.com",
)
LOW_QUALITY_DOMAINS = (
    "seekingalpha.com",
    "reddit.com",
    "stocktwits.com",
    "tipranks.com",
)


def classify_source(url: str, *, publisher: str | None = None) -> SourceQuality:
    """Return a deterministic quality label for a URL."""
    host = _host(url) or (publisher or "").lower()
    if not host:
        return SourceQuality("unknown", "unknown", 0.3, "No host available.")

    if host.endswith("sec.gov") or "sec.gov" in host:
        return SourceQuality("primary_filing", "primary", 1.0, "SEC EDGAR primary disclosure.")
    if any(token in host for token in ("investor.", ".ir.", "ir.")):
        return SourceQuality("company_ir", "primary", 0.95, "Company investor relations source.")
    if any(host.endswith(domain) for domain in REPUTABLE_FINANCIAL_DOMAINS):
        return SourceQuality("financial_press", "reputable", 0.8, "Reputable financial publication.")
    if any(host.endswith(domain) for domain in LOW_QUALITY_DOMAINS):
        return SourceQuality("market_commentary", "low", 0.25, "Use only as context; prefer primary sourcing.")
    if host.endswith(".gov"):
        return SourceQuality("regulatory", "primary", 0.9, "Government or regulatory source.")
    return SourceQuality("web", "standard", 0.55, "Unclassified web source; verify important claims.")


def enrich_source(source: Source) -> Source:
    """Return ``source`` with quality metadata filled when missing."""
    quality = classify_source(source.url, publisher=source.publisher)
    update = {
        "source_type": getattr(source, "source_type", None) or quality.source_type,
        "quality_tier": getattr(source, "quality_tier", None) or quality.quality_tier,
        "quality_score": getattr(source, "quality_score", None) or quality.quality_score,
        "quality_notes": getattr(source, "quality_notes", None) or quality.quality_notes,
    }
    return source.model_copy(update=update)


def enrich_sources(sources: list[Source]) -> list[Source]:
    return [enrich_source(source) for source in sources]


def build_evidence_claims(
    results: list[ResearchResult] | list[dict[str, Any]],
    sources: list[Source] | list[dict[str, Any]],
    *,
    max_claims: int = 12,
) -> list[dict[str, Any]]:
    """Build a lightweight claim/excerpt ledger from provider results.

    This is not a semantic fact extractor. It is a deterministic audit aid:
    each row points a downstream analyst to a concrete snippet and its URL.
    """
    source_by_url: dict[str, Source] = {}
    for raw in sources:
        try:
            source = raw if isinstance(raw, Source) else Source.model_validate(raw)
        except Exception:
            continue
        source_by_url[source.url] = enrich_source(source)

    claims: list[dict[str, Any]] = []
    for raw in results:
        try:
            result = raw if isinstance(raw, ResearchResult) else ResearchResult.model_validate(raw)
        except Exception:
            continue
        if not result.url or not result.content:
            continue
        source = source_by_url.get(result.url) or enrich_source(Source(url=result.url, title=result.title))
        excerpt = _clean_excerpt(result.content)
        if not excerpt:
            continue
        claims.append(
            {
                "claim": excerpt,
                "url": result.url,
                "title": result.title,
                "published_date": result.published_date,
                "search_score": result.score,
                "source_type": source.source_type,
                "quality_tier": source.quality_tier,
                "quality_score": source.quality_score,
                "excerpt": excerpt,
            }
        )
        if len(claims) >= max_claims:
            break
    return claims


def _host(url: str) -> str | None:
    try:
        return (urlparse(url).hostname or "").lower().lstrip("www.") or None
    except Exception:
        return None


_SPACE_RE = re.compile(r"\s+")


def _clean_excerpt(text: str, *, max_chars: int = 360) -> str:
    cleaned = _SPACE_RE.sub(" ", text).strip()
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned
    cut = cleaned[:max_chars].rsplit(" ", 1)[0].rstrip(".,;:")
    return f"{cut}..."
