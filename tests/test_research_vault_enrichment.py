"""Phase E integration: existing tools surface vault data when present.

Verifies:
- ``research_company`` returns a ``fundamentals_snapshot`` with ``source: 'vault'``
  when parsed JSON exists for the ticker.
- The vault file appears as a source in the merged sources list.
- ``build_investment_memo`` flips ``vault_data_used=True`` and its
  instructions string references the user's own DCF.
- When no vault data is present, behavior is unchanged: ``fundamentals_snapshot``
  is FMP-shaped or ``None``, memo instructions match the baseline string.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import openpyxl
import pytest

from investor_research.clients.fmp import FmpSnapshot
from investor_research.models import ResearchEvidence
from investor_research.tools import memo as memo_module
from investor_research.tools import research as research_module
from investor_research.vault.snapshots import import_xlsx


def _build_minimal_workbook(path, ticker: str = "MSFT") -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Main"
    ws.cell(row=3, column=1, value="Cleaned Ticker")
    ws.cell(row=3, column=2, value=f"{ticker} Equity")
    ws.cell(row=3, column=3, value=f"{ticker} Inc")
    ws.cell(row=5, column=1, value="Local Currency")
    ws.cell(row=5, column=2, value="USD")
    ws.cell(row=8, column=1, value="Scale")
    ws.cell(row=8, column=2, value=1_000_000)
    ws.cell(row=24, column=4, value="FISCAL_YEAR_PERIOD")
    ws.cell(row=24, column=5, value="2024 Y")
    ws.cell(row=24, column=6, value="2025 Y")
    ws.cell(row=27, column=3, value="Revenue")
    ws.cell(row=27, column=4, value="SALES_REV_TURN")
    ws.cell(row=27, column=5, value=220000)
    ws.cell(row=27, column=6, value=240000)
    ws.cell(row=190, column=10, value="Intrinsic value per share")
    ws.cell(row=190, column=11, value=480.0)
    ws.cell(row=193, column=10, value="Target price")
    ws.cell(row=193, column=11, value=520.0)
    wb.save(path)


@pytest.fixture
def vault_with_msft(settings_with_vault, tmp_path):
    src = tmp_path / "msft_export.xlsx"
    _build_minimal_workbook(src, "MSFT")
    import_xlsx(settings_with_vault, "MSFT", src)
    return settings_with_vault


def _patch_research(monkeypatch, settings, evidence=None, snapshot=None) -> tuple[MagicMock, MagicMock]:
    """Stub the research_provider + FMP client used by research.research_company."""
    fake_provider = MagicMock()
    fake_provider.search.return_value = evidence or ResearchEvidence(
        provider=settings.research_provider, query="q", answer="A", results=[], sources=[]
    )
    fake_fmp = MagicMock()
    fake_fmp.fetch_snapshot.return_value = snapshot or FmpSnapshot(ticker="MSFT")
    monkeypatch.setattr(research_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        research_module, "make_research_provider", lambda s: fake_provider
    )
    monkeypatch.setattr(
        research_module, "FmpClient", lambda *a, **kw: fake_fmp
    )
    return fake_provider, fake_fmp


def test_research_company_attaches_vault_block_when_parsed_exists(
    monkeypatch, vault_with_msft
) -> None:
    _patch_research(monkeypatch, vault_with_msft)
    result = research_module.research_company("MSFT")
    block = result["fundamentals_snapshot"]
    assert block is not None
    assert block["source"] == "vault"
    assert block["periods"] == ["FY2024", "FY2025"]
    assert block["series"]["revenue"] == [220000.0, 240000.0]
    assert block["dcf"]["target_price"] == 520.0
    # Vault source URL appears in the merged source list.
    vault_urls = [s["url"] for s in result["sources"] if s["url"].startswith("file://")]
    assert vault_urls, "expected vault source URL in result['sources']"
    # And the notes call out that vault data is present.
    assert any("vault" in n for n in result["notes"])


def test_research_company_falls_back_to_fmp_when_no_vault(
    monkeypatch, settings_with_vault
) -> None:
    # Vault is configured but MSFT has not been imported yet.
    snap = FmpSnapshot(
        ticker="MSFT",
        profile={"companyName": "Microsoft Corp", "sector": "IT"},
        quote={"price": 470.0, "marketCap": 3_500_000},
    )
    _patch_research(monkeypatch, settings_with_vault, snapshot=snap)
    result = research_module.research_company("MSFT")
    block = result["fundamentals_snapshot"]
    assert block is not None
    assert block["source"] == "fmp"
    assert block.get("profile", {}).get("companyName") == "Microsoft Corp"


def test_research_company_unchanged_when_vault_disabled(
    monkeypatch, settings_tavily_with_fmp
) -> None:
    snap = FmpSnapshot(ticker="MSFT", profile={"companyName": "Microsoft Corp"})
    _patch_research(monkeypatch, settings_tavily_with_fmp, snapshot=snap)
    result = research_module.research_company("MSFT")
    block = result["fundamentals_snapshot"]
    assert block is not None
    assert block["source"] == "fmp"


def test_build_investment_memo_flags_vault_and_updates_instructions(
    monkeypatch, vault_with_msft
) -> None:
    _patch_research(monkeypatch, vault_with_msft)
    monkeypatch.setattr(memo_module, "get_settings", lambda: vault_with_msft)
    result = memo_module.build_investment_memo("MSFT")
    assert result["vault_data_used"] is True
    assert "vault" in result["instructions"].lower()
    assert "target_price" in result["instructions"]
    # All five upstream tools ran and produced a vault-sourced fundamentals block
    # in at least research_company.
    rc = result["evidence_by_topic"]["research_company"]
    assert rc["fundamentals_snapshot"]["source"] == "vault"


def test_build_investment_memo_baseline_when_no_vault_data(
    monkeypatch, settings_tavily_with_fmp
) -> None:
    snap = FmpSnapshot(ticker="MSFT", profile={"companyName": "Microsoft Corp"})
    _patch_research(monkeypatch, settings_tavily_with_fmp, snapshot=snap)
    monkeypatch.setattr(memo_module, "get_settings", lambda: settings_tavily_with_fmp)
    result = memo_module.build_investment_memo("MSFT")
    assert result["vault_data_used"] is False
    # Instructions are the baseline form when no vault data is available.
    assert "fundamentals_snapshot" not in result["instructions"]
