"""Minimal observability helpers (structured logs).

Goal:
- One JSON line per notable event.
- No prompt/response bodies.
- Easy to grep and easy to parse later.

Env:
- LOG_LEVEL (default INFO)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional


_logger: Optional[logging.Logger] = None


def get_logger(name: str = "llm_council") -> logging.Logger:
    """Return a configured logger."""
    global _logger
    if _logger is not None:
        return _logger

    level_str = os.getenv("LOG_LEVEL", "INFO").upper().strip()
    level = getattr(logging, level_str, logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers in reload/dev environments.
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        formatter = logging.Formatter("%(message)s")  # raw JSON line
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.propagate = False
    _logger = logger
    return logger


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def log_event(event: Dict[str, Any], *, logger: Optional[logging.Logger] = None) -> None:
    """Emit one structured log line.

    Recommended fields:
      - event: str
      - run_id: str

    Keep this small. Do not include prompt/response bodies.
    """
    if logger is None:
        logger = get_logger()

    payload = {"ts": _utc_now_iso(), **event}

    try:
        logger.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        # Never crash the app because logging failed.
        logger.info(str(payload))
