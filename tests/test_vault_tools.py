"""Tests for the MCP-facing vault tool wrappers."""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest

from investor_research.tools import vault_tools


def _build_minimal_workbook(path: Path, ticker: str = "MSFT") -> None:
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
    wb.save(path)


def _patch_settings(monkeypatch, settings) -> None:
    monkeypatch.setattr(vault_tools, "get_settings", lambda: settings)


def test_list_vault_companies_returns_error_when_vault_unset(monkeypatch, settings_tavily) -> None:
    _patch_settings(monkeypatch, settings_tavily)
    out = vault_tools.list_vault_companies()
    assert out["ok"] is False
    assert out["error"] == "VAULT_NOT_CONFIGURED"


def test_list_vault_companies_returns_empty(monkeypatch, settings_with_vault) -> None:
    _patch_settings(monkeypatch, settings_with_vault)
    out = vault_tools.list_vault_companies()
    assert out["ok"] is True
    assert out["count"] == 0
    assert out["companies"] == []


def test_create_vault_company_idempotent(monkeypatch, settings_with_vault) -> None:
    _patch_settings(monkeypatch, settings_with_vault)
    first = vault_tools.create_vault_company("NKE", company_name="Nike Inc", sector="Consumer")
    assert first["ok"] is True
    assert Path(first["path"]).is_dir()
    second = vault_tools.create_vault_company("NKE")
    assert second["ok"] is True
    assert second["path"] == first["path"]


def test_import_xlsx_export_full_flow(
    monkeypatch, settings_with_vault, tmp_path: Path
) -> None:
    _patch_settings(monkeypatch, settings_with_vault)
    src = tmp_path / "msft.xlsx"
    _build_minimal_workbook(src)
    out = vault_tools.import_xlsx_export("MSFT", str(src))
    assert out["ok"] is True
    assert out["deduped"] is False
    assert out["summary"]["periods"] == 2
    assert "revenue" in out["summary"]["series_keys"]


def test_import_xlsx_export_missing_file(
    monkeypatch, settings_with_vault, tmp_path: Path
) -> None:
    _patch_settings(monkeypatch, settings_with_vault)
    out = vault_tools.import_xlsx_export("MSFT", str(tmp_path / "nope.xlsx"))
    assert out["ok"] is False
    assert out["error"] == "FILE_NOT_FOUND"


def test_load_company_data_before_import(monkeypatch, settings_with_vault) -> None:
    _patch_settings(monkeypatch, settings_with_vault)
    vault_tools.create_vault_company("MSFT", company_name="Microsoft")
    out = vault_tools.load_company_data("MSFT")
    assert out["ok"] is True
    assert out["has_parsed_data"] is False
    assert "fundamentals" not in out


def test_load_company_data_after_import(
    monkeypatch, settings_with_vault, tmp_path: Path
) -> None:
    _patch_settings(monkeypatch, settings_with_vault)
    src = tmp_path / "msft.xlsx"
    _build_minimal_workbook(src)
    vault_tools.import_xlsx_export("MSFT", str(src))
    out = vault_tools.load_company_data("MSFT")
    assert out["ok"] is True
    assert out["has_parsed_data"] is True
    assert out["fundamentals"]["series"]["revenue"] == [220000.0, 240000.0]
    assert "Profile" in out["prompt_block"]
    # JSON-serializable.
    json.dumps(out, default=str)


def test_write_company_note_preserves_frontmatter(
    monkeypatch, settings_with_vault
) -> None:
    _patch_settings(monkeypatch, settings_with_vault)
    vault_tools.create_vault_company("NKE", company_name="Nike Inc", sector="Consumer")
    out = vault_tools.write_company_note_tool("NKE", "# New body\n\nProse.\n")
    assert out["ok"] is True
    assert out["frontmatter"]["ticker"] == "NKE"
    assert out["frontmatter"]["sector"] == "Consumer"


def test_write_memo_to_vault_creates_file(
    monkeypatch, settings_with_vault
) -> None:
    _patch_settings(monkeypatch, settings_with_vault)
    out = vault_tools.write_memo_to_vault_tool(
        "NKE",
        "# Memo\n\nVerdict: WATCH.\n",
        memo_type="investment_memo",
        title="Initiating Coverage",
        date="2026-05-18",
    )
    assert out["ok"] is True
    assert Path(out["path"]).is_file()
    assert Path(out["path"]).name.startswith("2026-05-18_investment_memo")


def test_list_company_memos_tool(monkeypatch, settings_with_vault) -> None:
    _patch_settings(monkeypatch, settings_with_vault)
    vault_tools.write_memo_to_vault_tool(
        "NKE", "body", memo_type="earnings_update", date="2026-01-15"
    )
    vault_tools.write_memo_to_vault_tool(
        "NKE", "body", memo_type="investment_memo", date="2026-05-18"
    )
    out = vault_tools.list_company_memos_tool("NKE")
    assert out["ok"] is True
    assert out["count"] == 2
    assert out["memos"][0]["date"] == "2026-05-18"
