"""Minimal observability helpers (structured logs).

Goal:
- One JSON line per notable event.
- No prompt/response bodies.
- Easy to grep and easy to parse later.

Env:
- LOG_LEVEL (default INFO)
"""

from __future__ import annotations

import contextvars

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


_logger: Optional[logging.Logger] = None

# Propagate a run_id within the current async/task context.
_current_run_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "llm_council_run_id",
    default=None,
)


def set_run_id(run_id: Optional[str]) -> None:
    """Set the current run_id for this execution context."""
    _current_run_id.set(run_id)


def get_run_id() -> Optional[str]:
    """Get the current run_id for this execution context."""
    return _current_run_id.get()


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


def _log_file_for_run(run_id: Optional[str]) -> Optional[Path]:
    """Return per-run log path at data/logs/<run_id>.jsonl, creating the folder if needed."""
    if not run_id:
        return None
    base = Path("data") / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{run_id}.jsonl"


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

    # Resolve run_id for file logging:
    # 1) explicit event.run_id
    # 2) ambient context run_id (set via set_run_id)
    # 3) fallback identifiers (id / conversation_id) if present
    run_id = event.get("run_id") or get_run_id() or event.get("id") or event.get("conversation_id")
    if "run_id" not in payload and run_id:
        payload["run_id"] = run_id

    log_path = _log_file_for_run(run_id)
    if log_path is not None:
        try:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            pass  # never fail the app because file logging failed

    try:
        logger.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        # Never crash the app because logging failed.
        logger.info(str(payload))
