"""Analysis cache (rule 48).

Caches content/perceptual hashes, card detection, alignment, OCR, vision, and
region results to reduce latency. All entries have a content_hash key derived
from the media bytes.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class AnalysisCache:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._lock = threading.Lock()
        self._store: Dict[str, Dict[str, Any]] = {}  # key -> {kind -> value}

    def get(self, key: str, kind: str) -> Optional[Any]:
        if not self.enabled:
            return None
        with self._lock:
            return self._store.get(key, {}).get(kind)

    def set(self, key: str, kind: str, value: Any) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._store.setdefault(key, {})[kind] = value

    def has(self, key: str, kind: str) -> bool:
        return self.get(key, kind) is not None

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._store)
