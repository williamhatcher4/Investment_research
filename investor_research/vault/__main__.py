"""CLI entry point: ``python -m investor_research.vault <command>``.

Commands:
- ``init``      — create the top-level vault folder structure.
- ``reindex``   — rebuild the SQLite index from parsed JSON (added in Phase C).
"""

from __future__ import annotations

import argparse
import logging
import sys

from investor_research.config import get_settings
from investor_research.logging_setup import configure_logging
from investor_research.vault.paths import (
    VaultNotConfiguredError,
    initialize_vault,
)

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m investor_research.vault",
        description="Manage the local investment research vault.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser(
        "init",
        help="Create the top-level vault folder structure under VAULT_ROOT.",
    )
    sub.add_parser(
        "reindex",
        help="Rebuild the SQLite index from parsed JSON files. (Phase C)",
    )
    return parser


def cmd_init() -> int:
    settings = get_settings()
    try:
        root = initialize_vault(settings)
    except VaultNotConfiguredError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Initialized vault at: {root}")
    return 0


def cmd_reindex() -> int:
    # Implementation lands in Phase C with the database module.
    try:
        from investor_research.vault.database import reindex_vault
    except ImportError:
        print(
            "error: SQLite index not available yet (Phase C). "
            "Configure VAULT_ROOT and run `init` for now.",
            file=sys.stderr,
        )
        return 2
    settings = get_settings()
    try:
        result = reindex_vault(settings)
    except VaultNotConfiguredError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"Reindexed: {result.get('companies', 0)} companies, "
        f"{result.get('metrics', 0)} metrics, "
        f"{result.get('source_files', 0)} source files."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level, secrets=settings.secrets)

    if args.cmd == "init":
        return cmd_init()
    if args.cmd == "reindex":
        return cmd_reindex()
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
