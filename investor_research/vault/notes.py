"""Read/write Obsidian-style notes with YAML frontmatter.

Three artifact types live in the vault per company:

- ``{TICKER}.md`` — the company home note (frontmatter + freeform prose).
- ``memos/{date}_{slug}.md`` — dated, generated memos.
- ``notes/{slug}.md`` — ad-hoc human notes.

Frontmatter is YAML between two ``---`` fences at the very start of the
file. We parse and rewrite it in place, preserving the body verbatim.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from investor_research.config import Settings
from investor_research.tickers import validate_ticker
from investor_research.vault.paths import get_company_path

logger = logging.getLogger(__name__)


# ---- Frontmatter parsing ------------------------------------------------


_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<meta>.*?)\n---\s*\n?(?P<body>.*)$",
    re.DOTALL,
)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a string into ``(frontmatter_dict, body)``.

    When no frontmatter block is present, returns ``({}, text)``.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        meta = yaml.safe_load(match.group("meta")) or {}
    except yaml.YAMLError as exc:
        logger.warning("vault.notes.bad_frontmatter", extra={"err": str(exc)[:200]})
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, match.group("body")


def serialize_frontmatter(meta: dict[str, Any], body: str) -> str:
    """Render ``meta`` + ``body`` back into a single markdown string."""
    if not meta:
        return body
    yaml_block = yaml.safe_dump(
        meta, default_flow_style=False, sort_keys=False
    ).rstrip()
    body = body.lstrip("\n")
    return f"---\n{yaml_block}\n---\n\n{body}"


# ---- Company home note --------------------------------------------------


def company_note_path(settings: Settings, ticker: str) -> Path:
    """Return ``Companies/{TICKER}/{TICKER}.md``."""
    ticker = validate_ticker(ticker)
    return get_company_path(settings, ticker) / f"{ticker}.md"


def read_company_note(settings: Settings, ticker: str) -> str | None:
    """Return the raw markdown contents of the company home note, or None."""
    path = company_note_path(settings, ticker)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def read_company_frontmatter(settings: Settings, ticker: str) -> dict[str, Any]:
    """Return the parsed frontmatter dict (empty when no note / no frontmatter)."""
    text = read_company_note(settings, ticker)
    if text is None:
        return {}
    meta, _body = parse_frontmatter(text)
    return meta


def write_company_note(
    settings: Settings,
    ticker: str,
    body: str,
    frontmatter: dict[str, Any] | None = None,
    *,
    merge_frontmatter: bool = True,
) -> Path:
    """Write the company home note.

    - When ``frontmatter`` is provided and the note already exists, it is
      merged with the existing frontmatter (new keys win) unless
      ``merge_frontmatter=False``.
    - ``last_updated`` is auto-stamped to today's UTC date.
    - The body is written verbatim under the frontmatter block.
    """
    ticker = validate_ticker(ticker)
    path = company_note_path(settings, ticker)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing_meta: dict[str, Any] = {}
    if path.is_file():
        existing_meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))

    if frontmatter is None:
        meta = existing_meta
    elif merge_frontmatter:
        meta = {**existing_meta, **frontmatter}
    else:
        meta = dict(frontmatter)

    meta.setdefault("ticker", ticker)
    meta["last_updated"] = datetime.now(timezone.utc).date().isoformat()

    path.write_text(serialize_frontmatter(meta, body), encoding="utf-8")
    return path


# ---- Memos --------------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    return _SLUG_RE.sub("_", value.lower()).strip("_") or "memo"


def write_memo_to_vault(
    settings: Settings,
    ticker: str,
    memo_markdown: str,
    memo_type: str = "investment_memo",
    *,
    title: str | None = None,
    date: str | None = None,
) -> Path:
    """Save a dated memo to ``Companies/{TICKER}/memos/``.

    File name format: ``{YYYY-MM-DD}_{memo_type_slug}.md`` (or with
    ``_{title_slug}`` appended when a title is provided).
    """
    ticker = validate_ticker(ticker)
    memos_dir = get_company_path(settings, ticker) / "memos"
    memos_dir.mkdir(parents=True, exist_ok=True)

    date_str = date or datetime.now(timezone.utc).date().isoformat()
    slug_parts = [_slugify(memo_type)]
    if title:
        slug_parts.append(_slugify(title))
    file_name = f"{date_str}_{'_'.join(slug_parts)}.md"
    path = memos_dir / file_name

    # Prepend a small frontmatter block so the memo is Obsidian-queryable.
    meta: dict[str, Any] = {
        "ticker": ticker,
        "memo_type": memo_type,
        "date": date_str,
    }
    if title:
        meta["title"] = title
    path.write_text(serialize_frontmatter(meta, memo_markdown), encoding="utf-8")
    return path


_MEMO_NAME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<rest>.+)\.md$"
)


def list_company_memos(settings: Settings, ticker: str) -> list[dict[str, Any]]:
    """List memos for a ticker, newest first.

    Returns a list of ``{path, date, memo_type, title, file_name}`` dicts.
    Memos with non-conforming file names are still included but with
    ``date=None``.
    """
    ticker = validate_ticker(ticker)
    memos_dir = get_company_path(settings, ticker) / "memos"
    if not memos_dir.is_dir():
        return []

    out: list[dict[str, Any]] = []
    for path in memos_dir.glob("*.md"):
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        match = _MEMO_NAME_RE.match(path.name)
        date = meta.get("date") or (match.group("date") if match else None)
        memo_type = meta.get("memo_type") or (
            match.group("rest").split("_", 1)[0] if match else None
        )
        out.append(
            {
                "path": str(path),
                "file_name": path.name,
                "date": date,
                "memo_type": memo_type,
                "title": meta.get("title"),
            }
        )
    out.sort(key=lambda d: (d["date"] or "", d["file_name"]), reverse=True)
    return out
