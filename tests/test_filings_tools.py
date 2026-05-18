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


def test_extract_10k_risks_combines_edgar_and_search(monkeypatch, settings_tavily):
    fake_edgar = MagicMock()
    fake_edgar.latest_filing.return_value = _FILING
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
    assert out["answer"] == "Cyber, currency, M&A integration."
    assert out["sources"][0]["url"] == "https://sec.gov/foo"
    assert out["edgar_error"] is None


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
