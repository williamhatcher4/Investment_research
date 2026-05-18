"""Tests for the provider factory."""

from __future__ import annotations

from investor_research.clients.research_provider import make_research_provider


def test_factory_returns_tavily(settings_tavily, monkeypatch):
    captured = {}

    class FakeTavily:
        def __init__(self, settings):
            captured["settings"] = settings

    monkeypatch.setattr(
        "investor_research.clients.tavily.TavilyClient", FakeTavily
    )
    provider = make_research_provider(settings_tavily)
    assert isinstance(provider, FakeTavily)
    assert captured["settings"] is settings_tavily


def test_factory_returns_perplexity(settings_perplexity, monkeypatch):
    captured = {}

    class FakePplx:
        def __init__(self, settings):
            captured["settings"] = settings

    monkeypatch.setattr(
        "investor_research.clients.perplexity.PerplexityClient", FakePplx
    )
    provider = make_research_provider(settings_perplexity)
    assert isinstance(provider, FakePplx)
    assert captured["settings"] is settings_perplexity
