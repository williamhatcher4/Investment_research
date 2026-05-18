"""Tests for vault note + memo read/write with YAML frontmatter."""

from __future__ import annotations

from investor_research.vault.notes import (
    list_company_memos,
    parse_frontmatter,
    read_company_frontmatter,
    read_company_note,
    serialize_frontmatter,
    write_company_note,
    write_memo_to_vault,
)
from investor_research.vault.paths import create_company_folder, get_company_path


def test_parse_frontmatter_returns_empty_when_absent() -> None:
    meta, body = parse_frontmatter("# Heading\n\nBody only.")
    assert meta == {}
    assert body == "# Heading\n\nBody only."


def test_parse_frontmatter_extracts_yaml() -> None:
    text = "---\nticker: NKE\nstatus: watching\n---\n\n# Nike\n"
    meta, body = parse_frontmatter(text)
    assert meta == {"ticker": "NKE", "status": "watching"}
    assert body == "# Nike\n"


def test_serialize_frontmatter_roundtrip() -> None:
    meta = {"ticker": "NKE", "status": "watching"}
    body = "# Nike\n\nBody.\n"
    text = serialize_frontmatter(meta, body)
    meta_again, body_again = parse_frontmatter(text)
    assert meta_again == meta
    assert body_again == body


def test_write_company_note_creates_with_frontmatter(settings_with_vault) -> None:
    create_company_folder(settings_with_vault, "NKE", company_name="Nike Inc")
    path = write_company_note(
        settings_with_vault,
        "NKE",
        "# Nike\n\nNew body.\n",
        frontmatter={"sector": "Consumer Discretionary"},
    )
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "sector: Consumer Discretionary" in text
    assert "# Nike" in text
    # Existing frontmatter (ticker, status) must be preserved on merge.
    meta = read_company_frontmatter(settings_with_vault, "NKE")
    assert meta["ticker"] == "NKE"
    assert meta["status"] == "watching"
    assert meta["sector"] == "Consumer Discretionary"


def test_write_company_note_replace_mode(settings_with_vault) -> None:
    create_company_folder(settings_with_vault, "NKE", company_name="Nike Inc")
    write_company_note(
        settings_with_vault,
        "NKE",
        "body",
        frontmatter={"sector": "Consumer Discretionary"},
        merge_frontmatter=False,
    )
    meta = read_company_frontmatter(settings_with_vault, "NKE")
    # In replace mode the only original keys carried over are the ones the
    # writer always stamps (ticker + last_updated).
    assert meta.get("status") is None
    assert meta["sector"] == "Consumer Discretionary"
    assert meta["ticker"] == "NKE"


def test_read_company_note_returns_none_when_missing(settings_with_vault) -> None:
    assert read_company_note(settings_with_vault, "NKE") is None
    assert read_company_frontmatter(settings_with_vault, "NKE") == {}


def test_write_memo_to_vault_creates_dated_file(settings_with_vault) -> None:
    create_company_folder(settings_with_vault, "NKE")
    path = write_memo_to_vault(
        settings_with_vault,
        "NKE",
        "# Memo body\n",
        memo_type="investment_memo",
        title="Initiating Coverage",
        date="2026-05-18",
    )
    assert path.parent == get_company_path(settings_with_vault, "NKE") / "memos"
    assert path.name == "2026-05-18_investment_memo_initiating_coverage.md"
    text = path.read_text(encoding="utf-8")
    assert "memo_type: investment_memo" in text
    assert "# Memo body" in text


def test_list_company_memos_orders_newest_first(settings_with_vault) -> None:
    create_company_folder(settings_with_vault, "NKE")
    write_memo_to_vault(
        settings_with_vault, "NKE", "older", memo_type="earnings_update", date="2026-01-15"
    )
    write_memo_to_vault(
        settings_with_vault, "NKE", "newer", memo_type="investment_memo", date="2026-05-18"
    )
    memos = list_company_memos(settings_with_vault, "NKE")
    assert [m["date"] for m in memos] == ["2026-05-18", "2026-01-15"]
    assert memos[0]["memo_type"] == "investment_memo"


def test_list_company_memos_empty_when_no_memos_dir(settings_with_vault) -> None:
    # Don't create the company folder; list should be empty (no exception).
    assert list_company_memos(settings_with_vault, "NKE") == []
