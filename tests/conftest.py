"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from investor_research.config import Settings, reset_settings_cache


@pytest.fixture(autouse=True)
def _env_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force a clean env so dotenv files don't leak real keys into tests."""
    for key in (
        "RESEARCH_PROVIDER",
        "TAVILY_API_KEY",
        "TAVILY_SEARCH_DEPTH",
        "PERPLEXITY_API_KEY",
        "PERPLEXITY_MODEL",
        "FMP_API_KEY",
        "LOG_LEVEL",
        "VAULT_ROOT",
        "ENABLE_SQLITE",
    ):
        monkeypatch.delenv(key, raising=False)
    # Stub out dotenv loading so a developer's real .env doesn't leak in
    # via get_settings() after we've cleared the env above.
    monkeypatch.setattr("investor_research.config.load_dotenv", lambda *a, **k: False)
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def settings_tavily() -> Settings:
    return Settings(
        research_provider="tavily",
        tavily_api_key="tvly-test-key-aaaa",
        tavily_search_depth="advanced",
        perplexity_api_key=None,
        perplexity_model="sonar-pro",
        fmp_api_key=None,
        log_level="DEBUG",
        secrets=("tvly-test-key-aaaa",),
    )


@pytest.fixture
def settings_tavily_with_fmp() -> Settings:
    return Settings(
        research_provider="tavily",
        tavily_api_key="tvly-test-key-aaaa",
        tavily_search_depth="advanced",
        perplexity_api_key=None,
        perplexity_model="sonar-pro",
        fmp_api_key="fmp-test-key-bbbb",
        log_level="DEBUG",
        secrets=("tvly-test-key-aaaa", "fmp-test-key-bbbb"),
    )


@pytest.fixture
def settings_perplexity() -> Settings:
    return Settings(
        research_provider="perplexity",
        tavily_api_key=None,
        tavily_search_depth="advanced",
        perplexity_api_key="pplx-test-key-aaaa",
        perplexity_model="sonar-pro",
        fmp_api_key=None,
        log_level="DEBUG",
        secrets=("pplx-test-key-aaaa",),
    )


@pytest.fixture
def settings_with_vault(tmp_path: Path) -> Settings:
    """Tavily-keyed settings pointed at a per-test temp vault directory."""
    return Settings(
        research_provider="tavily",
        tavily_api_key="tvly-test-key-aaaa",
        tavily_search_depth="advanced",
        perplexity_api_key=None,
        perplexity_model="sonar-pro",
        fmp_api_key=None,
        log_level="DEBUG",
        vault_root=tmp_path / "vault",
        enable_sqlite=True,
        secrets=("tvly-test-key-aaaa",),
    )


@pytest.fixture
def fake_openai_client() -> MagicMock:
    """A MagicMock shaped like the OpenAI SDK client used by PerplexityClient."""
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    return client


def make_sonar_response(content: str, citations: list | None = None) -> MagicMock:
    """Build a mock response object matching what the OpenAI SDK returns."""
    response = MagicMock()
    choice = MagicMock()
    choice.message = MagicMock()
    choice.message.content = content
    response.choices = [choice]
    response.citations = citations or []
    response.search_results = []
    usage = MagicMock()
    usage.model_dump = lambda: {"prompt_tokens": 100, "completion_tokens": 200}
    response.usage = usage
    return response


@pytest.fixture
def sonar_response_factory():
    return make_sonar_response


def make_tavily_response(
    *,
    answer: str | None = "Synthesized answer.",
    results: list[dict] | None = None,
) -> dict:
    """Build a dict matching what tavily-python's `search()` returns."""
    return {
        "query": "ignored",
        "answer": answer,
        "results": results
        or [
            {
                "title": "10-Q",
                "url": "https://sec.gov/foo",
                "content": "Revenue grew 12% YoY...",
                "score": 0.95,
                "published_date": "2026-04-25",
            }
        ],
        "response_time": 0.42,
    }


@pytest.fixture
def tavily_response_factory():
    return make_tavily_response


@pytest.fixture
def fake_tavily_sdk(tavily_response_factory) -> MagicMock:
    """A MagicMock shaped like a tavily-python TavilyClient."""
    client = MagicMock()
    client.search.return_value = tavily_response_factory()
    return client
