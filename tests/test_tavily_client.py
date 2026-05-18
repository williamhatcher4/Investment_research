"""Tests for the Tavily search client."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from investor_research.clients.tavily import TavilyClient, TavilyError


def test_search_happy_path(settings_tavily, fake_tavily_sdk):
    client = TavilyClient(settings_tavily, client=fake_tavily_sdk)
    evidence = client.search("MSFT business model", max_results=5)

    fake_tavily_sdk.search.assert_called_once()
    kwargs = fake_tavily_sdk.search.call_args.kwargs
    assert kwargs["search_depth"] == "advanced"
    assert kwargs["include_answer"] == "advanced"
    assert kwargs["max_results"] == 5
    assert "days" not in kwargs
    assert "topic" not in kwargs

    assert evidence.provider == "tavily"
    assert evidence.answer == "Synthesized answer."
    assert evidence.results[0].url == "https://sec.gov/foo"
    assert evidence.sources[0].publisher == "sec.gov"


def test_recency_param_triggers_news_topic(settings_tavily, fake_tavily_sdk):
    client = TavilyClient(settings_tavily, client=fake_tavily_sdk)
    client.search("MSFT news", recency_days=14)

    kwargs = fake_tavily_sdk.search.call_args.kwargs
    assert kwargs["days"] == 14
    assert kwargs["topic"] == "news"


def test_empty_answer_normalized_to_none(settings_tavily, tavily_response_factory):
    sdk = MagicMock()
    sdk.search.return_value = tavily_response_factory(answer="   ")
    client = TavilyClient(settings_tavily, client=sdk)
    evidence = client.search("foo")
    assert evidence.answer is None


def test_retries_on_transient_error(settings_tavily, tavily_response_factory):
    sdk = MagicMock()
    sdk.search.side_effect = [ConnectionError("flaky"), tavily_response_factory()]
    client = TavilyClient(settings_tavily, client=sdk)
    evidence = client.search("foo")
    assert sdk.search.call_count == 2
    assert evidence.answer == "Synthesized answer."


def test_persistent_failure_raises(settings_tavily):
    sdk = MagicMock()
    sdk.search.side_effect = ConnectionError("down")
    client = TavilyClient(settings_tavily, client=sdk)
    with pytest.raises(TavilyError):
        client.search("foo")
    assert sdk.search.call_count == 3


def test_sources_dedupe_by_url(settings_tavily, tavily_response_factory):
    sdk = MagicMock()
    sdk.search.return_value = tavily_response_factory(
        results=[
            {"title": "A", "url": "https://x.com/a", "content": "1"},
            {"title": "A dup", "url": "https://x.com/a", "content": "2"},
            {"title": "B", "url": "https://y.com/b", "content": "3"},
        ]
    )
    client = TavilyClient(settings_tavily, client=sdk)
    evidence = client.search("foo")
    urls = [s.url for s in evidence.sources]
    assert urls == ["https://x.com/a", "https://y.com/b"]
    # but results preserve duplicates (snippets are separately useful)
    assert len(evidence.results) == 3
