"""Tests for build_investment_memo (raw-evidence bundle mode)."""

from __future__ import annotations

import pytest

from investor_research.tools import memo as memo_module


def _evidence_dict(urls: list[str]) -> dict:
    return {
        "ticker": "MSFT",
        "sources": [{"url": u, "title": None, "publisher": None} for u in urls],
        "results": [],
        "answer": "answer",
    }


def test_dedupe_sources_collapses_duplicates():
    upstream = {
        "research_company": _evidence_dict(["https://a", "https://b"]),
        "recent_news": _evidence_dict(["https://b", "https://c"]),
        "earnings_summary": _evidence_dict([]),
        "competitors": {"sources": [{"url": "https://a"}]},
        "risks": _evidence_dict(["https://d"]),
    }
    sources, by_url = memo_module._dedupe_sources(upstream)
    urls = [s.url for s in sources]
    assert urls == ["https://a", "https://b", "https://c", "https://d"]
    assert by_url["https://b"] == 1


def test_build_investment_memo_parallel_dispatch(monkeypatch, settings_tavily):
    called = {name: 0 for name in (
        "research_company", "recent_news", "earnings_summary", "competitors", "risks"
    )}

    def _stub(name: str):
        def inner(*args, **kwargs):
            called[name] += 1
            return _evidence_dict([f"https://{name}.example.com"])
        return inner

    monkeypatch.setattr(memo_module, "research_company", _stub("research_company"))
    monkeypatch.setattr(memo_module, "get_recent_news", _stub("recent_news"))
    monkeypatch.setattr(memo_module, "get_earnings_summary", _stub("earnings_summary"))
    monkeypatch.setattr(memo_module, "get_competitors", _stub("competitors"))
    monkeypatch.setattr(memo_module, "get_risks", _stub("risks"))
    monkeypatch.setattr(memo_module, "get_settings", lambda: settings_tavily)

    out = memo_module.build_investment_memo("MSFT")

    assert called == {
        "research_company": 1, "recent_news": 1, "earnings_summary": 1,
        "competitors": 1, "risks": 1,
    }
    urls = sorted(s["url"] for s in out["sources"])
    assert urls == sorted([
        "https://research_company.example.com",
        "https://recent_news.example.com",
        "https://earnings_summary.example.com",
        "https://competitors.example.com",
        "https://risks.example.com",
    ])
    assert out["provider"] == "tavily"
    assert len(out["memo_outline"]) == 11
    assert "Watchlist Verdict" in out["memo_outline"]
    assert "evidence_by_topic" in out
    assert set(out["evidence_by_topic"].keys()) == set(called.keys())


def test_partial_upstream_failure_still_returns_bundle(monkeypatch, settings_tavily):
    monkeypatch.setattr(memo_module, "research_company", lambda t: _evidence_dict(["https://ok"]))
    monkeypatch.setattr(memo_module, "get_recent_news", lambda t, days=30: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(memo_module, "get_earnings_summary", lambda t: _evidence_dict([]))
    monkeypatch.setattr(memo_module, "get_competitors", lambda t: _evidence_dict([]))
    monkeypatch.setattr(memo_module, "get_risks", lambda t: _evidence_dict([]))
    monkeypatch.setattr(memo_module, "get_settings", lambda: settings_tavily)

    out = memo_module.build_investment_memo("MSFT")
    assert out["evidence_by_topic"]["recent_news"]["error"].startswith("boom")
    assert "https://ok" in [s["url"] for s in out["sources"]]
