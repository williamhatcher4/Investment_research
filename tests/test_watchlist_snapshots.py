"""End-to-end tests for the watchlist snapshot orchestrator."""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest
import yaml

from investor_research.tools import vault_tools
from investor_research.vault.snapshots import (
    WATCHLIST_INDEX_FILENAME,
    WATCHLIST_PEER_SET_NAME,
    import_watchlist_xlsx,
    load_company_watchlist,
    load_watchlist_snapshot,
)


def _build_watchlist(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "May 21 2026"
    # Required headers
    ws.cell(row=4, column=4, value="BB Ticker")
    ws.cell(row=4, column=5, value="Long Name")
    ws.cell(row=4, column=14, value="Tangible Post Tax")
    ws.cell(row=4, column=16, value="Post Tax")
    ws.cell(row=4, column=38, value="P/E1")
    ws.cell(row=4, column=70, value="Rating")
    ws.cell(row=4, column=72, value="Fair Value")
    ws.cell(row=5, column=7, value="Price")
    ws.cell(row=5, column=8, value="IV")
    ws.cell(row=5, column=9, value="MoS")

    # US row 1
    ws.cell(row=6, column=1, value=1)
    ws.cell(row=6, column=4, value="aapl us equity")
    ws.cell(row=6, column=5, value="Apple Inc")
    ws.cell(row=6, column=6, value="Information Technology")
    ws.cell(row=6, column=7, value=260.25)
    ws.cell(row=6, column=10, value=0.087)
    ws.cell(row=6, column=38, value=31.6)
    ws.cell(row=6, column=67, value="Wide")
    ws.cell(row=6, column=72, value=260)

    # US row 2 (BF/B)
    ws.cell(row=7, column=1, value=2)
    ws.cell(row=7, column=4, value="BF/B US Equity")
    ws.cell(row=7, column=5, value="Brown-Forman Corp")
    ws.cell(row=7, column=6, value="Consumer Staples")
    ws.cell(row=7, column=7, value=42.0)

    # Non-US row (no auto-resolution)
    ws.cell(row=8, column=1, value=3)
    ws.cell(row=8, column=4, value="ASML NA Equity")
    ws.cell(row=8, column=5, value="ASML Holding NV")
    ws.cell(row=8, column=6, value="Information Technology")
    ws.cell(row=8, column=7, value=900.0)

    wb.save(path)


def test_import_watchlist_xlsx_full_flow(
    settings_with_vault, tmp_path: Path
) -> None:
    src = tmp_path / "scorecard.xlsx"
    _build_watchlist(src)
    result = import_watchlist_xlsx(settings_with_vault, str(src))

    assert result["rows_total"] == 3
    assert result["rows_resolved"] == 3          # AAPL + BF.B + ASML (non-US auto-resolves too)
    assert result["rows_unresolved"] == 0
    assert result["unresolved_tickers"] == []
    assert result["deduped"] is False
    assert Path(result["xlsx_path"]).is_file()

    # Index file written
    index_path = Path(result["index_path"])
    assert index_path.is_file()
    assert index_path.name == WATCHLIST_INDEX_FILENAME

    # Dated history copy exists
    dated_path = Path(result["dated_path"])
    assert dated_path.is_file()
    assert "2026-05-21" in dated_path.name

    # Per-company fan-out for all three resolved tickers
    aapl_path = settings_with_vault.vault_root / "Companies" / "AAPL" / "parsed" / "watchlist.json"
    bfb_path = settings_with_vault.vault_root / "Companies" / "BF.B" / "parsed" / "watchlist.json"
    asml_path = settings_with_vault.vault_root / "Companies" / "ASML" / "parsed" / "watchlist.json"
    assert aapl_path.is_file()
    assert bfb_path.is_file()
    assert asml_path.is_file()
    aapl_payload = json.loads(aapl_path.read_text(encoding="utf-8"))
    assert aapl_payload["bloomberg_ticker"] == "aapl us equity"
    assert aapl_payload["normalized_ticker"] == "AAPL"
    assert aapl_payload["price"] == pytest.approx(260.25)
    assert aapl_payload["ratings"]["morningstar_moat"] == "Wide"
    asml_payload = json.loads(asml_path.read_text(encoding="utf-8"))
    assert asml_payload["normalized_ticker"] == "ASML"

    # Peer set "watchlist_coverage" updated with all resolved tickers
    peer_path = settings_with_vault.vault_root / "Watchlists" / "peer_sets.yml"
    assert peer_path.is_file()
    peer_data = yaml.safe_load(peer_path.read_text(encoding="utf-8"))
    assert WATCHLIST_PEER_SET_NAME in peer_data
    assert set(peer_data[WATCHLIST_PEER_SET_NAME]["tickers"]) == {"AAPL", "BF.B", "ASML"}

    # Aliases yaml auto-seeded for every parseable Bloomberg ticker
    aliases_path = settings_with_vault.vault_root / "Watchlists" / "ticker_aliases.yml"
    assert aliases_path.is_file()
    aliases = yaml.safe_load(aliases_path.read_text(encoding="utf-8"))
    assert aliases["aapl us equity"]["ticker"] == "AAPL"
    assert aliases["bf/b us equity"]["ticker"] == "BF.B"
    assert aliases["asml na equity"]["ticker"] == "ASML"
    assert aliases["asml na equity"]["country"] == "NL"


def test_import_watchlist_dedupes_same_file(
    settings_with_vault, tmp_path: Path
) -> None:
    src = tmp_path / "scorecard.xlsx"
    _build_watchlist(src)
    first = import_watchlist_xlsx(settings_with_vault, str(src))
    second = import_watchlist_xlsx(settings_with_vault, str(src))
    assert first["deduped"] is False
    assert second["deduped"] is True
    assert first["xlsx_path"] == second["xlsx_path"]


def test_load_watchlist_snapshot_roundtrip(
    settings_with_vault, tmp_path: Path
) -> None:
    src = tmp_path / "scorecard.xlsx"
    _build_watchlist(src)
    import_watchlist_xlsx(settings_with_vault, str(src))
    loaded = load_watchlist_snapshot(settings_with_vault)
    assert loaded is not None
    assert len(loaded.rows) == 3
    assert loaded.rows[0].normalized_ticker == "AAPL"
    assert loaded.sheet_name == "May 21 2026"
    assert loaded.snapshot_date == "2026-05-21"


def test_load_watchlist_snapshot_returns_none_when_missing(settings_with_vault) -> None:
    assert load_watchlist_snapshot(settings_with_vault) is None


def test_load_company_watchlist_after_import(
    settings_with_vault, tmp_path: Path
) -> None:
    src = tmp_path / "scorecard.xlsx"
    _build_watchlist(src)
    import_watchlist_xlsx(settings_with_vault, str(src))
    payload = load_company_watchlist(settings_with_vault, "AAPL")
    assert payload is not None
    assert payload["normalized_ticker"] == "AAPL"
    assert payload["bloomberg_ticker"] == "aapl us equity"


def test_load_company_watchlist_missing_returns_none(settings_with_vault) -> None:
    assert load_company_watchlist(settings_with_vault, "XYZ") is None


def test_mcp_tool_import_wraps_envelope(
    monkeypatch, settings_with_vault, tmp_path: Path
) -> None:
    monkeypatch.setattr(vault_tools, "get_settings", lambda: settings_with_vault)
    src = tmp_path / "scorecard.xlsx"
    _build_watchlist(src)
    out = vault_tools.import_watchlist_xlsx_export(str(src))
    assert out["ok"] is True
    assert out["rows_resolved"] == 3
    assert "as_of" in out


def test_mcp_tool_load_company_watchlist_missing(
    monkeypatch, settings_with_vault
) -> None:
    monkeypatch.setattr(vault_tools, "get_settings", lambda: settings_with_vault)
    out = vault_tools.load_company_watchlist_tool("AAPL")
    assert out["ok"] is True
    assert out["has_watchlist_data"] is False


def test_mcp_tool_import_vault_unset(monkeypatch, settings_tavily) -> None:
    monkeypatch.setattr(vault_tools, "get_settings", lambda: settings_tavily)
    out = vault_tools.import_watchlist_xlsx_export("/nonexistent.xlsx")
    assert out["ok"] is False
    assert out["error"] == "VAULT_NOT_CONFIGURED"


def test_reimport_removes_rows_dropped_from_watchlist(
    settings_with_vault, tmp_path: Path
) -> None:
    first = tmp_path / "first.xlsx"
    _build_watchlist(first)
    import_watchlist_xlsx(settings_with_vault, str(first))
    assert load_company_watchlist(settings_with_vault, "AAPL") is not None

    # Second workbook drops Apple.
    wb = openpyxl.load_workbook(first)
    wb.active.delete_rows(6)
    second = tmp_path / "second.xlsx"
    wb.save(second)
    result = import_watchlist_xlsx(settings_with_vault, str(second))

    assert load_company_watchlist(settings_with_vault, "AAPL") is None
    assert any("AAPL" in p for p in result["removed_stale_paths"])
    assert result["rows_resolved"] == 2
    assert load_company_watchlist(settings_with_vault, "BF.B") is not None
    assert load_company_watchlist(settings_with_vault, "ASML") is not None
