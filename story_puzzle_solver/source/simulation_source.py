"""SimulationStorySource — serves generated fixtures (rule 52).

Emulates a live story feed with artificial latencies and a configurable
publication schedule. This is the ONLY concrete source used by the test suite
and by ``SIMULATION=true`` mode. It can also inject network errors to exercise
robustness (rule 50).
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..common.logger import JsonLogger
from .base import StoryItem, StorySource


@dataclass
class _QueuedStory:
    story_id: str
    media_path: Path
    media_type: str
    publish_at: float  # monotonic seconds
    values: Dict[str, str] = field(default_factory=dict)
    revealed: Dict[str, bool] = field(default_factory=dict)


class SimulationStorySource(StorySource):
    def __init__(self, fixtures: List, poll_interval_ms: int = 250,
                 download_latency_ms: int = 80, jitter_ms: int = 30,
                 fail_once_at: Optional[int] = None,
                 logger: Optional[JsonLogger] = None):
        """``fixtures`` is a list of StoryFixture objects (from FixtureGenerator).

        Stories are published on a virtual timeline starting at ``start()``.
        ``download_latency_ms`` simulates media fetch latency.
        ``fail_once_at`` (if set) makes the Nth get_media raise, to test retry.
        """
        self._fixtures = list(fixtures)
        self._poll_interval_ms = poll_interval_ms
        self._download_latency_ms = download_latency_ms
        self._jitter_ms = jitter_ms
        self._fail_once_at = fail_once_at
        self._logger = logger or JsonLogger("sim_source")

        self._queue: List[_QueuedStory] = []
        self._delivered: List[str] = []
        self._latest: Optional[StoryItem] = None
        self._subs: List[Callable[[StoryItem], None]] = []
        self._lock = threading.Lock()
        self._connected = False
        self._start_t: Optional[float] = None
        self._get_count = 0
        self._spacing_s = 0.0

    def set_schedule(self, spacing_s: float = 0.0, start_delay_s: float = 0.0) -> None:
        """Control publication cadence. spacing_s between stories."""
        self._spacing_s = spacing_s

    def connect(self) -> bool:
        self._connected = True
        self._start_t = time.monotonic() + self._spacing_s  # first available immediately
        # build queue
        t = self._start_t
        self._queue.clear()
        for f in self._fixtures:
            qs = _QueuedStory(
                story_id=f.story_id, media_path=Path(f.media_path),
                media_type=f.media_type, publish_at=t,
                values=dict(getattr(f, "values", {})),
                revealed=dict(getattr(f, "revealed", {})),
            )
            self._queue.append(qs)
            t += self._spacing_s if self._spacing_s > 0 else 0.0
        self._logger.info("sim_connected", queued=len(self._queue))
        return True

    def poll(self) -> List[StoryItem]:
        if not self._connected:
            return []
        now = time.monotonic()
        out: List[StoryItem] = []
        with self._lock:
            remaining: List[_QueuedStory] = []
            for qs in self._queue:
                if qs.publish_at <= now:
                    item = StoryItem(
                        story_id=qs.story_id,
                        media_path=qs.media_path,
                        media_type_hint=qs.media_type,
                        timestamp=None,
                    )
                    out.append(item)
                    self._delivered.append(qs.story_id)
                    if self._latest is None or qs.story_id != getattr(self._latest, "story_id", None):
                        self._latest = item
                else:
                    remaining.append(qs)
            self._queue = remaining
        # notify subscribers
        for item in out:
            for cb in self._subs:
                try:
                    cb(item)
                except Exception as e:
                    self._logger.warn("sub_callback_error", error=str(e))
        return out

    def subscribe(self, callback: Callable[[StoryItem], None]) -> None:
        self._subs.append(callback)

    def get_latest(self) -> Optional[StoryItem]:
        return self._latest

    def get_media(self, story: StoryItem, dest: Path) -> Path:
        self._get_count += 1
        # simulated download latency
        delay = (self._download_latency_ms + random.randint(0, self._jitter_ms)) / 1000.0
        time.sleep(delay)
        if self._fail_once_at is not None and self._get_count == self._fail_once_at:
            self._fail_once_at = None
            raise ConnectionError("simulated network error")
        src = Path(story.media_path)
        if not src.exists():
            raise FileNotFoundError(f"media missing: {src}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        dest.write_bytes(data)
        return dest

    def disconnect(self) -> None:
        self._connected = False
        self._logger.info("sim_disconnected")

    @property
    def delivered(self) -> List[str]:
        return list(self._delivered)
