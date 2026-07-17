"""Structured JSON logging.

Every log record is emitted as a single JSON line with standard fields
(timestamp, level, logger, message) plus any extra key/value pairs passed
via ``logger.info("msg", extra={...})``.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

# Attributes every LogRecord has; anything else is treated as structured extra data.
_STANDARD_ATTRS = set(logging.makeLogRecord({}).__dict__.keys()) | {
    "message",
    "asctime",
}


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    # Remove existing handlers so repeated setup (tests / reload) never duplicates lines.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)

    # Quiet noisy third-party loggers.
    for noisy in ("telethon", "apscheduler", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
