"""Tests for the structured-logging + secret-redaction setup."""

from __future__ import annotations

import json
import logging
from io import StringIO

from investor_research.logging_setup import (
    JsonFormatter,
    SecretRedactionFilter,
    configure_logging,
)


def _build_capture_logger(secret: str) -> tuple[logging.Logger, StringIO]:
    buf = StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SecretRedactionFilter([secret]))
    logger = logging.getLogger(f"test.redact.{secret[:4]}")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger, buf


def test_redacts_known_secret() -> None:
    logger, buf = _build_capture_logger("super-secret-key")
    logger.info("calling api with super-secret-key in url")
    record = json.loads(buf.getvalue())
    assert "super-secret-key" not in record["msg"]
    assert "***REDACTED***" in record["msg"]


def test_no_redaction_when_secret_absent() -> None:
    logger, buf = _build_capture_logger("super-secret-key")
    logger.info("plain message with no key")
    record = json.loads(buf.getvalue())
    assert record["msg"] == "plain message with no key"


def test_configure_logging_is_idempotent() -> None:
    configure_logging("DEBUG", secrets=())
    configure_logging("INFO", secrets=("a",))
    root = logging.getLogger()
    assert len(root.handlers) == 1
