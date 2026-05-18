"""Tests for the config loader."""

from __future__ import annotations

import pytest

from investor_research.config import ConfigurationError, get_settings, reset_settings_cache


def test_tavily_default_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    reset_settings_cache()
    with pytest.raises(ConfigurationError, match="TAVILY_API_KEY"):
        get_settings()


def test_perplexity_provider_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCH_PROVIDER", "perplexity")
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    reset_settings_cache()
    with pytest.raises(ConfigurationError, match="PERPLEXITY_API_KEY"):
        get_settings()


def test_unknown_provider_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCH_PROVIDER", "google")
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    reset_settings_cache()
    with pytest.raises(ConfigurationError, match="RESEARCH_PROVIDER"):
        get_settings()


def test_tavily_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-1")
    reset_settings_cache()
    settings = get_settings()
    assert settings.research_provider == "tavily"
    assert settings.tavily_api_key == "tvly-1"
    assert settings.tavily_search_depth == "advanced"
    assert settings.secrets == ("tvly-1",)


def test_search_depth_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-1")
    monkeypatch.setenv("TAVILY_SEARCH_DEPTH", "extreme")
    reset_settings_cache()
    with pytest.raises(ConfigurationError, match="SEARCH_DEPTH"):
        get_settings()


def test_perplexity_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCH_PROVIDER", "perplexity")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-1")
    monkeypatch.setenv("FMP_API_KEY", "fmp-1")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    reset_settings_cache()
    settings = get_settings()
    assert settings.research_provider == "perplexity"
    assert settings.perplexity_api_key == "pplx-1"
    assert settings.fmp_api_key == "fmp-1"
    assert settings.log_level == "DEBUG"
    assert "pplx-1" in settings.secrets and "fmp-1" in settings.secrets
