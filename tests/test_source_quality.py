"""Tests for source-quality metadata and evidence ledgers."""

from __future__ import annotations

from investor_research.models import ResearchResult, Source
from investor_research.source_quality import build_evidence_claims, classify_source, enrich_source


def test_classify_sec_as_primary() -> None:
    quality = classify_source("https://www.sec.gov/Archives/foo.htm")
    assert quality.source_type == "primary_filing"
    assert quality.quality_tier == "primary"
    assert quality.quality_score == 1.0


def test_enrich_source_adds_quality_metadata() -> None:
    source = enrich_source(Source(url="https://www.reuters.com/markets/company"))
    assert source.source_type == "financial_press"
    assert source.quality_tier == "reputable"


def test_build_evidence_claims_from_results() -> None:
    claims = build_evidence_claims(
        [
            ResearchResult(
                title="10-K",
                url="https://www.sec.gov/foo",
                content="Revenue increased because of higher enterprise demand. More text.",
                score=0.91,
            )
        ],
        [Source(url="https://www.sec.gov/foo")],
    )
    assert claims[0]["quality_tier"] == "primary"
    assert "Revenue increased" in claims[0]["claim"]
