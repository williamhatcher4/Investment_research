"""Local investment research vault.

The vault is an Obsidian-compatible folder structure that stores per-company
research artifacts:

- Raw Bloomberg/FactSet xlsx exports (``bloomberg_exports/``, immutable)
- Parsed normalized JSON (``parsed/``, canonical structured data)
- Human-written notes (``NKE.md``, ``notes/``)
- Generated memos (``memos/``)

JSON files in ``parsed/`` are the canonical structured store. The optional
SQLite index (``database/investor_research.sqlite``) is a rebuildable
projection over the parsed JSON, used only for cross-company queries.

Public API is exported here; see ``paths``, ``notes``, ``xlsx_parser``,
``snapshots``, and ``database`` submodules for the implementations.
"""

from __future__ import annotations

from investor_research.vault.paths import (
    VaultNotConfiguredError,
    create_company_folder,
    get_company_path,
    get_latest_xlsx_export,
    get_vault_root,
    initialize_vault,
    list_companies,
    vault_enabled,
)
from investor_research.vault.snapshots import (
    import_xlsx,
    load_parsed_json,
    refresh_from_latest_xlsx,
    write_parsed_json,
)
from investor_research.vault.xlsx_parser import (
    XlsxParseError,
    parse_workbook,
    sha256_of,
)

__all__ = [
    "VaultNotConfiguredError",
    "XlsxParseError",
    "create_company_folder",
    "get_company_path",
    "get_latest_xlsx_export",
    "get_vault_root",
    "import_xlsx",
    "initialize_vault",
    "list_companies",
    "load_parsed_json",
    "parse_workbook",
    "refresh_from_latest_xlsx",
    "sha256_of",
    "vault_enabled",
    "write_parsed_json",
]
