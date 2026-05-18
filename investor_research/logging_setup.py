"""Structured JSON logging with secret-redaction filter."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Iterable


class SecretRedactionFilter(logging.Filter):
    """Replace any occurrence of a known secret with ``***REDACTED***``."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = tuple(s for s in secrets if s)

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        msg = record.getMessage()
        redacted = msg
        for secret in self._secrets:
            if secret and secret in redacted:
                redacted = redacted.replace(secret, "***REDACTED***")
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _STD_LOGRECORD_KEYS or key.startswith("_"):
                continue
            try:
                json.dumps(value)
            except TypeError:
                value = repr(value)
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


_STD_LOGRECORD_KEYS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message",
}


_configured = False


def configure_logging(level: str, secrets: Iterable[str] = ()) -> None:
    """Install handlers, formatter, and redaction filter on the root logger.

    Idempotent: safe to call from both the MCP entry point and tests.
    """
    global _configured

    root = logging.getLogger()
    root.setLevel(level.upper())

    # stderr — Claude Desktop captures stdout for MCP stdio frames.
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SecretRedactionFilter(secrets))

    if _configured:
        root.handlers.clear()
    root.addHandler(handler)
    _configured = True
