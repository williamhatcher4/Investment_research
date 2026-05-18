"""Configuration loading and validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv


class ConfigurationError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


ProviderName = Literal["tavily", "perplexity"]
SUPPORTED_PROVIDERS: tuple[ProviderName, ...] = ("tavily", "perplexity")


@dataclass(frozen=True)
class Settings:
    research_provider: ProviderName
    tavily_api_key: str | None = None
    tavily_search_depth: str = "advanced"
    perplexity_api_key: str | None = None
    perplexity_model: str = "sonar-pro"
    fmp_api_key: str | None = None
    sec_edgar_user_agent: str = "investor-research-mcp contact@example.com"
    log_level: str = "INFO"
    request_timeout_seconds: float = 60.0
    vault_root: Path | None = None
    enable_sqlite: bool = True
    secrets: tuple[str, ...] = field(default_factory=tuple)

    @property
    def vault_enabled(self) -> bool:
        """True when ``vault_root`` is configured (the path need not exist yet)."""
        return self.vault_root is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load environment configuration. Cached: env is read once per process."""
    load_dotenv()

    provider_raw = os.environ.get("RESEARCH_PROVIDER", "tavily").strip().lower() or "tavily"
    if provider_raw not in SUPPORTED_PROVIDERS:
        raise ConfigurationError(
            f"RESEARCH_PROVIDER must be one of {SUPPORTED_PROVIDERS}, got {provider_raw!r}"
        )
    provider: ProviderName = provider_raw  # type: ignore[assignment]

    tavily_key = os.environ.get("TAVILY_API_KEY", "").strip() or None
    perplexity_key = os.environ.get("PERPLEXITY_API_KEY", "").strip() or None

    if provider == "tavily" and not tavily_key:
        raise ConfigurationError(
            "TAVILY_API_KEY is required when RESEARCH_PROVIDER=tavily."
        )
    if provider == "perplexity" and not perplexity_key:
        raise ConfigurationError(
            "PERPLEXITY_API_KEY is required when RESEARCH_PROVIDER=perplexity."
        )

    fmp_key = os.environ.get("FMP_API_KEY", "").strip() or None
    model = os.environ.get("PERPLEXITY_MODEL", "sonar-pro").strip() or "sonar-pro"
    log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    depth = os.environ.get("TAVILY_SEARCH_DEPTH", "advanced").strip().lower() or "advanced"
    if depth not in ("basic", "advanced"):
        raise ConfigurationError(
            f"TAVILY_SEARCH_DEPTH must be 'basic' or 'advanced', got {depth!r}"
        )

    user_agent = (
        os.environ.get("SEC_EDGAR_USER_AGENT", "").strip()
        or "investor-research-mcp contact@example.com"
    )

    vault_root_raw = os.environ.get("VAULT_ROOT", "").strip()
    vault_root = Path(vault_root_raw).expanduser().resolve() if vault_root_raw else None

    enable_sqlite_raw = os.environ.get("ENABLE_SQLITE", "true").strip().lower()
    if enable_sqlite_raw not in ("true", "false", "1", "0", "yes", "no", ""):
        raise ConfigurationError(
            f"ENABLE_SQLITE must be a boolean, got {enable_sqlite_raw!r}"
        )
    enable_sqlite = enable_sqlite_raw in ("true", "1", "yes", "")

    secrets = tuple(v for v in (tavily_key, perplexity_key, fmp_key) if v)

    return Settings(
        research_provider=provider,
        tavily_api_key=tavily_key,
        tavily_search_depth=depth,
        perplexity_api_key=perplexity_key,
        perplexity_model=model,
        fmp_api_key=fmp_key,
        sec_edgar_user_agent=user_agent,
        log_level=log_level,
        vault_root=vault_root,
        enable_sqlite=enable_sqlite,
        secrets=secrets,
    )


def reset_settings_cache() -> None:
    """Test helper: clear the cached Settings so env reloads on next call."""
    get_settings.cache_clear()
