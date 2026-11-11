"""Tests for tools/filings.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from investor_research.clients.sec_edgar import FilingEntry, SecEdgarError
from investor_research.models import ResearchEvidence, ResearchResult, Source
from investor_research.tools import filings as filings_module
from investor_research.tools.research import InvalidTickerError

_FILING = FilingEntry(
    form="10-K",
    filing_date="2024-07-30",
    report_date="2024-06-30",
    accession_number="0001564590-24-031420",
    primary_document="msft-20240630.htm",
    filing_url="https://www.sec.gov/Archives/edgar/data/789019/000156459024031420/msft-20240630.htm",
    index_url="https://www.sec.gov/Archives/edgar/data/789019/000156459024031420/0001564590-24-031420-index.htm",
)


def test_get_sec_filings_validates_ticker(monkeypatch, settings_tavily):
    monkeypatch.setattr(filings_module, "get_settings", lambda: settings_tavily)
    with pytest.raises(InvalidTickerError):
        filings_module.get_sec_filings("1bad")


def test_get_sec_filings_returns_grouped_payload(monkeypatch, settings_tavily):
    fake_edgar = MagicMock()
    fake_edgar.get_filings.return_value = {
        "ticker": "MSFT",
        "cik": "0000789019",
        "company_name": "MICROSOFT CORP",
        "filings": {"10-K": [_FILING.__dict__]},
    }
    monkeypatch.setattr(filings_module, "SecEdgarClient", lambda settings: fake_edgar)
    monkeypatch.setattr(filings_module, "get_settings", lambda: settings_tavily)

    out = filings_module.get_sec_filings("MSFT", forms=["10-K"], limit_per_form=1)
    assert out["source"] == "SEC EDGAR"
    assert out["company_name"] == "MICROSOFT CORP"
    assert out["filings"]["10-K"][0]["primary_document"] == "msft-20240630.htm"


def test_get_sec_filings_unknown_ticker_returns_error(monkeypatch, settings_tavily):
    fake_edgar = MagicMock()
    fake_edgar.get_filings.side_effect = SecEdgarError("no CIK")
    monkeypatch.setattr(filings_module, "SecEdgarClient", lambda settings: fake_edgar)
    monkeypatch.setattr(filings_module, "get_settings", lambda: settings_tavily)

    out = filings_module.get_sec_filings("ZZZZ")
    assert out["error"] == "no CIK"
    assert out["filings"] == {}


def test_extract_10k_risks_skips_search_when_sec_extraction_succeeds(monkeypatch, settings_tavily):
    fake_edgar = MagicMock()
    fake_edgar.latest_filing.return_value = _FILING
    fake_edgar.fetch_filing_text.return_value = """
    <html><body>
    <h1>Item 1A. Risk Factors</h1><p>We face cybersecurity and macroeconomic risks.</p>
    <h1>Item 1B. Unresolved Staff Comments</h1>
    <h1>Item 7. Management's Discussion and Analysis</h1><p>Revenue grew.</p>
    <h1>Item 7A. Quantitative and Qualitative Disclosures</h1><p>Currency risk.</p>
    <h1>Item 8. Financial Statements</h1>
    </body></html>
    """
    monkeypatch.setattr(filings_module, "SecEdgarClient", lambda settings: fake_edgar)

    fake_provider = MagicMock()
    fake_provider.search.return_value = ResearchEvidence(
        provider="tavily",
        query="MSFT 10-K risk factors",
        answer="Cyber, currency, M&A integration.",
        results=[ResearchResult(url="https://sec.gov/foo")],
        sources=[Source(url="https://sec.gov/foo")],
    )
    monkeypatch.setattr(filings_module, "make_research_provider", lambda s: fake_provider)
    monkeypatch.setattr(filings_module, "get_settings", lambda: settings_tavily)

    out = filings_module.extract_10k_risks("MSFT")
    assert out["primary_filing"]["filing_date"] == "2024-07-30"
    assert out["filing_sections"]["item_1a"]["found"] is True
    assert "cybersecurity" in out["filing_sections"]["item_1a"]["text"]
    assert out["evidence_claims"][0]["quality_tier"] == "primary"
    assert out["answer"] is None
    assert out["search_skipped"] is True
    fake_provider.search.assert_not_called()
    assert out["sources"][0]["url"] == _FILING.filing_url
    assert out["edgar_error"] is None


def test_extract_filing_sections_plain_text() -> None:
    sections = filings_module.extract_filing_sections(
        "Item 1A. Risk Factors\nRisk one.\nItem 1B. Other\nStop.",
        {"item_1a": ("1A", ("1B",))},
    )
    assert sections["item_1a"]["found"] is True
    assert "Risk one" in sections["item_1a"]["text"]


def test_extract_10k_risks_handles_missing_10k(monkeypatch, settings_tavily):
    fake_edgar = MagicMock()
    fake_edgar.latest_filing.return_value = None
    monkeypatch.setattr(filings_module, "SecEdgarClient", lambda settings: fake_edgar)

    fake_provider = MagicMock()
    fake_provider.search.return_value = ResearchEvidence(
        provider="tavily", query="q", answer=None, results=[], sources=[]
    )
    monkeypatch.setattr(filings_module, "make_research_provider", lambda s: fake_provider)
    monkeypatch.setattr(filings_module, "get_settings", lambda: settings_tavily)

    out = filings_module.extract_10k_risks("MSFT")
    assert out["primary_filing"] is None
    assert "No 10-K found" in out["notes"][0]
    assert out["search_skipped"] is False


def test_extract_10k_risks_handles_edgar_error(monkeypatch, settings_tavily):
    fake_edgar = MagicMock()
    fake_edgar.latest_filing.side_effect = SecEdgarError("EDGAR 500")
    monkeypatch.setattr(filings_module, "SecEdgarClient", lambda settings: fake_edgar)

    fake_provider = MagicMock()
    fake_provider.search.return_value = ResearchEvidence(
        provider="tavily", query="q", answer="fallback", results=[], sources=[]
    )
    monkeypatch.setattr(filings_module, "make_research_provider", lambda s: fake_provider)
    monkeypatch.setattr(filings_module, "get_settings", lambda: settings_tavily)

    out = filings_module.extract_10k_risks("MSFT")
    assert out["primary_filing"] is None
    assert out["edgar_error"] == "EDGAR 500"
    assert out["answer"] == "fallback"  # search still ran
    assert out["search_skipped"] is False


def test_extract_filing_sections_skips_table_of_contents() -> None:
    from investor_research.tools.filings import extract_filing_sections

    html = """<html><body><h2>TABLE OF CONTENTS</h2><table>
    <tr><td>Item 1.</td><td>Business</td><td>3</td></tr>
    <tr><td>Item 1A.</td><td>Risk Factors</td><td>12</td></tr>
    <tr><td>Item 1B.</td><td>Unresolved Staff Comments</td><td>25</td></tr>
    <tr><td>Item 7.</td><td>MD&amp;A</td><td>30</td></tr></table>
    <p>Item 1. Business</p><p>We make widgets.</p>
    <p>Item 1A. Risk Factors</p>
    <p>Our supplier concentration could hurt margins. Demand is cyclical.</p>
    <p>See Item 7. for more detail.</p>
    <p>Item 1B. Unresolved Staff Comments</p><p>None.</p>
    <p>Item 7. Management's Discussion and Analysis</p><p>Revenue grew.</p>
    <p>Item 8. Financial Statements</p></body></html>"""
    sections = extract_filing_sections(
        html, {"item_1a": ("1A", ("1B", "2")), "item_7": ("7", ("7A", "8"))}
    )
    risk = sections["item_1a"]
    assert risk["found"] is True
    assert "supplier concentration" in risk["text"]
    assert "Unresolved" not in risk["text"]
    item_7 = sections["item_7"]["text"]
    assert item_7.startswith("Item 7. Management")
    assert "Revenue grew" in item_7
    assert "Unresolved" not in item_7
