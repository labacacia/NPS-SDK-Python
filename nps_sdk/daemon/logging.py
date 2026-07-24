# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Single-line JSON structured logging (port of ``JsonStructuredLogging`` +
``NpsJsonConsoleFormatter``).

Each record carries the fields the operator runbook expects: ``timestamp``,
``level``, ``msg``, ``logger``, and ``trace_id`` (when set). The minimum level
is read from the ``NPS_LOG_LEVEL`` environment variable
(trace/debug/info/warning/error/critical/none — case-insensitive), matching the
.NET reference.
"""

from __future__ import annotations

import datetime
import json
import logging
import os

LOG_LEVEL_ENV_VAR = "NPS_LOG_LEVEL"

# Map operator-facing names to Python logging levels (and back for output).
_NAME_TO_LEVEL = {
    "trace": logging.DEBUG,   # Python has no TRACE; fold into DEBUG.
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "information": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
    "none": logging.CRITICAL + 10,
}

_LEVELNO_TO_NAME = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warn",
    logging.ERROR: "error",
    logging.CRITICAL: "critical",
}


def resolve_log_level(fallback: int = logging.INFO) -> int:
    """Resolve the configured level from ``NPS_LOG_LEVEL``, else ``fallback``."""
    raw = os.environ.get(LOG_LEVEL_ENV_VAR)
    if not raw or not raw.strip():
        return fallback
    return _NAME_TO_LEVEL.get(raw.strip().lower(), fallback)


def _level_name(levelno: int) -> str:
    if levelno in _LEVELNO_TO_NAME:
        return _LEVELNO_TO_NAME[levelno]
    # Fold custom levels to the nearest standard bucket.
    for threshold in (logging.CRITICAL, logging.ERROR, logging.WARNING,
                      logging.INFO, logging.DEBUG):
        if levelno >= threshold:
            return _LEVELNO_TO_NAME[threshold]
    return "debug"


class NpsJsonFormatter(logging.Formatter):
    """Single-line JSON log formatter for NPS daemons."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.datetime.fromtimestamp(
            record.created, tz=datetime.timezone.utc).isoformat()
        payload: dict[str, object] = {
            "timestamp": ts,
            "level": _level_name(record.levelno),
            "msg": record.getMessage(),
            "logger": record.name,
        }
        trace_id = getattr(record, "trace_id", None)
        if trace_id:
            payload["trace_id"] = trace_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"))


def configure_json_logging(default_level: int = logging.INFO,
                           stream=None) -> logging.Handler:
    """Install the JSON formatter on the root logger and honour
    ``NPS_LOG_LEVEL``. Returns the configured handler.

    Clears existing root handlers (mirrors .NET ``ClearProviders``) so output is
    exclusively single-line JSON.
    """
    level = resolve_log_level(default_level)
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(NpsJsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    return handler
