"""Structured JSON logger.

Rule 42: logs are JSON structured. Full detected values are NOT written by
default — callers pass ``value=False`` or omit values. Only metadata
(region, confidence, latency, event) is logged.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


_LOCK = threading.Lock()


class JsonLogger:
    """Thread-safe JSON lines logger."""

    def __init__(self, name: str, log_dir: Optional[Path] = None, debug: bool = False):
        self.name = name
        self.debug = debug
        self._file = None
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            path = log_dir / f"{name}.jsonl"
            # open in append/text, line-buffered
            self._file = open(path, "a", encoding="utf-8")
        # also mirror to stderr for live visibility (compact)
        self._stderr = logging.getLogger(f"sps.{name}")

    def _emit(self, event: str, payload: dict[str, Any], level: str = "info") -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "logger": self.name,
            "level": level,
            "event": event,
            **payload,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _LOCK:
            if self._file is not None:
                self._file.write(line + "\n")
                self._file.flush()
            # stderr only in debug to avoid noise (rule 60)
            if self.debug or level in {"error", "warn"}:
                print(line, file=sys.stderr, flush=True)

    def info(self, event: str, **payload: Any) -> None:
        self._emit(event, payload, "info")

    def warn(self, event: str, **payload: Any) -> None:
        self._emit(event, payload, "warn")

    def error(self, event: str, **payload: Any) -> None:
        self._emit(event, payload, "error")

    def debug_log(self, event: str, **payload: Any) -> None:
        if self.debug:
            self._emit(event, payload, "debug")

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


_DEFAULT: Optional[JsonLogger] = None
_DEFAULT_LOCK = threading.Lock()


def get_logger(name: str = "sps", log_dir: Optional[Path] = None, debug: bool = False) -> JsonLogger:
    """Return a logger. A shared default is created on first call."""
    global _DEFAULT
    if _DEFAULT is None:
        with _DEFAULT_LOCK:
            if _DEFAULT is None:
                _DEFAULT = JsonLogger(name, log_dir, debug)
    return _DEFAULT
