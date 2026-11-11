"""Phase 2 filing tools: get_sec_filings and extract_10k_risks."""

from __future__ import annotations

import logging
import re
from html import unescape
from datetime import datetime, timezone
from typing import Any

from investor_research.clients.research_provider import make_research_provider
from investor_research.clients.sec_edgar import SecEdgarClient, SecEdgarError
from investor_research.config import get_settings
from investor_research.models import Source
from investor_research.prompts.research import EXTRACT_10K_RISKS_QUERY
from investor_research.source_quality import build_evidence_claims, enrich_source, enrich_sources
from investor_research.tools.research import _validate_ticker

logger = logging.getLogger(__name__)


def get_sec_filings(
    ticker: str,
    forms: list[str] | None = None,
    limit_per_form: int = 4,
) -> dict[str, Any]:
    """Return recent SEC filings (10-K, 10-Q, 8-K, proxy) for ``ticker``.

    Pulled directly from EDGAR — these are primary sources.
    """
    ticker = _validate_ticker(ticker)
    if not isinstance(limit_per_form, int) or limit_per_form <= 0 or limit_per_form > 50:
        raise ValueError("limit_per_form must be a positive integer ≤ 50")

    forms_tuple = tuple(forms) if forms else ("10-K", "10-Q", "8-K", "proxy")
    edgar = SecEdgarClient(get_settings())

    try:
        result = edgar.get_filings(ticker, forms=forms_tuple, limit_per_form=limit_per_form)
    except SecEdgarError as exc:
        return {
            "ticker": ticker,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
            "filings": {},
        }

    return {
        **result,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "source": "SEC EDGAR",
    }


def extract_10k_risks(ticker: str) -> dict[str, Any]:
    """Surface evidence about Item 1A Risk Factors in the most recent 10-K.

    Returns the primary 10-K filing URL (from EDGAR) plus a Tavily search
    constrained to sec.gov so Claude downstream can summarize risk factors
    grounded in the actual filing.
    """
    ticker = _validate_ticker(ticker)
    settings = get_settings()
    edgar = SecEdgarClient(settings)

    try:
        latest = edgar.latest_filing(ticker, "10-K")
    except SecEdgarError as exc:
        latest = None
        edgar_error = str(exc)
    else:
        edgar_error = None

    filing_sections: dict[str, Any] = {}
    filing_text_error = None
    if latest is not None:
        try:
            filing_text = edgar.fetch_filing_text(latest.filing_url)
            filing_sections = extract_filing_sections(
                filing_text,
                {
                    "item_1a": ("1A", ("1B", "2")),
                    "item_7": ("7", ("7A", "8")),
                    "item_7a": ("7A", ("8",)),
                },
            )
        except Exception as exc:  # noqa: BLE001 - retain search fallback
            filing_text_error = str(exc)

    query = EXTRACT_10K_RISKS_QUERY.format(ticker=ticker)
    extraction_succeeded = bool(
        isinstance(filing_sections.get("item_1a"), dict)
        and filing_sections["item_1a"].get("found")
    )
    search_skipped = extraction_succeeded and filing_text_error is None
    evidence_answer = None
    evidence_results = []
    evidence_sources = []
    if not search_skipped:
        provider = make_research_provider(settings)
        evidence = provider.search(query, max_results=10)
        evidence_answer = evidence.answer
        evidence_results = evidence.results
        evidence_sources = evidence.sources
    filing_source = (
        enrich_source(
            Source(
                url=latest.filing_url,
                title=f"{ticker} latest 10-K primary filing",
                publisher="SEC EDGAR",
            )
        )
        if latest is not None
        else None
    )
    sources = list(evidence_sources)
    if filing_source is not None and all(src.url != filing_source.url for src in sources):
        sources.insert(0, filing_source)
    section_claims = _section_claims(filing_sections, filing_source)

    return {
        "ticker": ticker,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "provider": settings.research_provider,
        "primary_filing": latest.__dict__ if latest else None,
        "edgar_error": edgar_error,
        "filing_text_error": filing_text_error,
        "filing_sections": filing_sections,
        "query": query,
        "answer": evidence_answer,
        "results": [r.model_dump(mode="json") for r in evidence_results],
        "sources": [s.model_dump(mode="json") for s in enrich_sources(sources)],
        "evidence_claims": section_claims
        + build_evidence_claims(evidence_results, sources),
        "search_skipped": search_skipped,
        "notes": (
            ["No 10-K found on EDGAR for this ticker."]
            if latest is None and edgar_error is None
            else []
        ),
    }


# ---- Filing text extraction --------------------------------------------


def extract_filing_sections(
    html_or_text: str,
    section_specs: dict[str, tuple[str, tuple[str, ...]]],
    *,
    max_chars: int = 20000,
) -> dict[str, Any]:
    """Extract SEC filing sections by Item heading.

    ``section_specs`` maps output keys to ``(start_item, end_items)``. The
    parser is intentionally conservative and dependency-free; it works best on
    10-K HTML converted to text but also accepts already-plain text.
    """
    text = _html_to_text(html_or_text)
    out: dict[str, Any] = {}
    for key, (start_item, end_items) in section_specs.items():
        # A 10-K mentions each Item several times: in the table of contents,
        # in mid-sentence cross-references ("see Item 7"), and at the real
        # heading. Headings start a line, so prefer those; then keep the
        # longest span, since table-of-contents entries are a line long.
        starts = _find_item_headings(text, start_item)
        best: tuple[int, int] | None = None
        for i, start in enumerate(starts):
            stops = [
                pos
                for item in end_items
                if (pos := _find_item_heading(text, item, start + 20)) is not None
            ]
            if i + 1 < len(starts):
                stops.append(starts[i + 1])
            # Any later line-start "Item N" heading also closes the section.
            next_heading = _ANY_ITEM_HEADING_RE.search(text, start + 20)
            if next_heading:
                stops.append(next_heading.start())
            end = min(stops) if stops else len(text)
            if best is None or end - start > best[1] - best[0]:
                best = (start, end)
        if best is None:
            out[key] = {"found": False, "text": "", "char_count": 0}
            continue
        section_text = text[best[0]:best[1]].strip()
        truncated = len(section_text) > max_chars
        if truncated:
            section_text = section_text[:max_chars].rsplit(" ", 1)[0].strip() + "..."
        out[key] = {
            "found": True,
            "text": section_text,
            "char_count": len(section_text),
            "truncated": truncated,
        }
    return out


def _html_to_text(html_or_text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html_or_text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>|</div>|</tr>|</h[1-6]>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_ANY_ITEM_HEADING_RE = re.compile(r"(?im)^[ \t]*item\s+\d{1,2}[a-c]?\.?\s+")


def _item_heading_re(item: str, *, line_start: bool) -> re.Pattern[str]:
    prefix = r"(?im)^[ \t]*" if line_start else r"(?i)\b"
    return re.compile(rf"{prefix}item\s+{re.escape(item)}\.?\s+")


def _find_item_headings(text: str, item: str) -> list[int]:
    """Positions of ``Item <item>`` headings; line-start matches preferred."""
    for line_start in (True, False):
        found = [m.start() for m in _item_heading_re(item, line_start=line_start).finditer(text)]
        if found:
            return found
    return []


def _find_item_heading(text: str, item: str, start: int = 0) -> int | None:
    for line_start in (True, False):
        match = _item_heading_re(item, line_start=line_start).search(text, start)
        if match:
            return match.start()
    return None


def _section_claims(
    filing_sections: dict[str, Any],
    filing_source: Source | None,
) -> list[dict[str, Any]]:
    if filing_source is None:
        return []
    out: list[dict[str, Any]] = []
    for section_name, payload in filing_sections.items():
        if not isinstance(payload, dict) or not payload.get("found"):
            continue
        text = str(payload.get("text") or "").strip()
        if not text:
            continue
        excerpt = text[:500].rsplit(" ", 1)[0].strip()
        out.append(
            {
                "claim": f"Extracted {section_name.upper()} text from latest 10-K.",
                "url": filing_source.url,
                "title": filing_source.title,
                "source_type": filing_source.source_type,
                "quality_tier": filing_source.quality_tier,
                "quality_score": filing_source.quality_score,
                "excerpt": excerpt,
            }
        )
    return out
