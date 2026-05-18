"""Tests for the Perplexity Sonar client wrapper."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest
from openai import APIError, RateLimitError

from investor_research.clients.perplexity import (
    PerplexityClient,
    PerplexityError,
    _strip_titles,
)
from investor_research.models import RecentNewsPayload


def test_query_returns_content_and_sources(settings_perplexity, fake_openai_client, sonar_response_factory):
    fake_openai_client.chat.completions.create.return_value = sonar_response_factory(
        content='{"days_window": 30, "events": [], "interpretation": null}',
        citations=["https://sec.gov/foo", {"url": "https://ir.example.com", "title": "IR"}],
    )
    client = PerplexityClient(settings_perplexity, client=fake_openai_client)

    resp = client.query("sys", "user")
    assert "days_window" in resp.content
    urls = [s.url for s in resp.sources]
    assert urls == ["https://sec.gov/foo", "https://ir.example.com"]


def test_search_returns_research_evidence(settings_perplexity, fake_openai_client, sonar_response_factory):
    fake_openai_client.chat.completions.create.return_value = sonar_response_factory(
        content="MSFT had a strong quarter.",
        citations=["https://sec.gov/foo"],
    )
    client = PerplexityClient(settings_perplexity, client=fake_openai_client)

    evidence = client.search("MSFT earnings", recency_days=30)
    assert evidence.provider == "perplexity"
    assert evidence.answer == "MSFT had a strong quarter."
    assert evidence.sources[0].url == "https://sec.gov/foo"

    user_msg = fake_openai_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "last 30 days" in user_msg


def test_query_structured_parses_payload(settings_perplexity, fake_openai_client, sonar_response_factory):
    payload_json = json.dumps(
        {
            "days_window": 30,
            "events": [],
            "interpretation": "Strong quarter.",
        }
    )
    fake_openai_client.chat.completions.create.return_value = sonar_response_factory(
        content=payload_json, citations=["https://sec.gov/foo"]
    )
    client = PerplexityClient(settings_perplexity, client=fake_openai_client)
    parsed, sources = client.query_structured("sys", "user", RecentNewsPayload)
    assert isinstance(parsed, RecentNewsPayload)
    assert sources[0].url == "https://sec.gov/foo"


def test_invalid_json_raises(settings_perplexity, fake_openai_client, sonar_response_factory):
    fake_openai_client.chat.completions.create.return_value = sonar_response_factory(
        content="not json"
    )
    client = PerplexityClient(settings_perplexity, client=fake_openai_client)
    with pytest.raises(PerplexityError):
        client.query_structured("sys", "user", RecentNewsPayload)


def test_retries_on_rate_limit(settings_perplexity, fake_openai_client, sonar_response_factory):
    transient = _make_rate_limit_error()
    success = sonar_response_factory(content="ok")
    fake_openai_client.chat.completions.create.side_effect = [transient, success]
    client = PerplexityClient(settings_perplexity, client=fake_openai_client)
    resp = client.query("sys", "user")
    assert fake_openai_client.chat.completions.create.call_count == 2
    assert resp.content == "ok"


def test_persistent_failure_raises(settings_perplexity, fake_openai_client):
    err = APIError("boom", request=MagicMock(), body=None)
    fake_openai_client.chat.completions.create.side_effect = err
    client = PerplexityClient(settings_perplexity, client=fake_openai_client)
    with pytest.raises(PerplexityError):
        client.query("sys", "user")
    assert fake_openai_client.chat.completions.create.call_count == 3


def test_strip_titles_helper_recurses() -> None:
    schema = {
        "title": "Root",
        "type": "object",
        "properties": {
            "x": {"title": "X", "type": "string"},
        },
    }
    out = _strip_titles(schema)
    assert "title" not in out
    assert "title" not in out["properties"]["x"]


def _make_rate_limit_error() -> RateLimitError:
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.perplexity.ai/x"))
    return RateLimitError("rate limit", response=response, body=None)
