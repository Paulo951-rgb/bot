"""Central configuration loader.

All tunables are read from environment variables (with a `.env` file loaded if
present). Nothing in the application hardcodes competition times, resolutions,
or thresholds — everything flows from :class:`Config`.

Rule 5: COMPETITION_START / EXPECTED_EVENT_TIME / COMPETITION_END must be
configurable and never coded directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional


class RunMode(str, Enum):
    NORMAL = "NORMAL_MODE"
    PREPARATION = "PREPARATION_MODE"
    COMPETITION = "COMPETITION_MODE"


def _bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: Optional[str], default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def _float(value: Optional[str], default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def _parse_hhmm(value: Optional[str], default: str) -> time:
    raw = value.strip() if value else default
    try:
        h, m = raw.split(":")
        return time(int(h), int(m))
    except Exception:
        h, m = default.split(":")
        return time(int(h), int(m))


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no external dependency)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


@dataclass
class Config:
    # Competition window
    competition_start: time
    expected_event_time: time
    competition_end: time

    # Polling
    poll_interval_ms: int

    # Video sampling
    video_initial_sample_ms: int
    video_focused_sample_ms: int
    video_max_frames: int

    # Engines
    ocr_enabled: bool
    vision_enabled: bool
    ocr_workers: int
    vision_workers: int

    # Card normalization
    card_width: int
    card_height: int

    # Cache
    cache_enabled: bool

    # Notifications
    windows_notifications: bool

    # Modes
    simulation: bool
    debug_mode: bool
    simulation_latency_ms: int
    simulation_jitter_ms: int

    # OCR
    ocr_confidence_high: float
    ocr_confidence_medium: float
    ocr_digit_whitelist: str
    ocr_min_confirmations: int

    # Notification threshold
    notify_min_confidence: float

    # Storage
    state_dir: Path
    log_dir: Path

    # Dashboard
    dashboard_host: str
    dashboard_port: int

    # Paths
    project_root: Path = field(default_factory=lambda: Path.cwd())

    @classmethod
    def load(cls, env_path: Optional[Path] = None) -> "Config":
        root = Path(__file__).resolve().parents[2]
        if env_path is None:
            env_path = root / ".env"
        _load_dotenv(env_path)

        g = os.environ.get
        return cls(
            competition_start=_parse_hhmm(g("COMPETITION_START"), "22:50"),
            expected_event_time=_parse_hhmm(g("EXPECTED_EVENT_TIME"), "23:00"),
            competition_end=_parse_hhmm(g("COMPETITION_END"), "23:30"),
            poll_interval_ms=_int(g("POLL_INTERVAL_MS"), 250),
            video_initial_sample_ms=_int(g("VIDEO_INITIAL_SAMPLE_MS"), 300),
            video_focused_sample_ms=_int(g("VIDEO_FOCUSED_SAMPLE_MS"), 75),
            video_max_frames=_int(g("VIDEO_MAX_FRAMES"), 400),
            ocr_enabled=_bool(g("OCR_ENABLED"), True),
            vision_enabled=_bool(g("VISION_ENABLED"), True),
            ocr_workers=_int(g("OCR_WORKERS"), 2),
            vision_workers=_int(g("VISION_WORKERS"), 1),
            card_width=_int(g("CARD_WIDTH"), 1024),
            card_height=_int(g("CARD_HEIGHT"), 640),
            cache_enabled=_bool(g("CACHE_ENABLED"), True),
            windows_notifications=_bool(g("WINDOWS_NOTIFICATIONS"), True),
            simulation=_bool(g("SIMULATION"), True),
            debug_mode=_bool(g("DEBUG_MODE"), False),
            simulation_latency_ms=_int(g("SIMULATION_LATENCY_MS"), 10),
            simulation_jitter_ms=_int(g("SIMULATION_JITTER_MS"), 3),
            ocr_confidence_high=_float(g("OCR_CONFIDENCE_HIGH"), 0.90),
            ocr_confidence_medium=_float(g("OCR_CONFIDENCE_MEDIUM"), 0.70),
            ocr_digit_whitelist=g("OCR_DIGIT_WHITELIST") or "0123456789",
            ocr_min_confirmations=_int(g("OCR_MIN_CONFIRMATIONS"), 2),
            notify_min_confidence=_float(g("NOTIFY_MIN_CONFIDENCE"), 0.75),
            state_dir=Path(g("STATE_DIR") or ".state"),
            log_dir=Path(g("LOG_DIR") or ".logs"),
            dashboard_host=g("DASHBOARD_HOST") or "127.0.0.1",
            dashboard_port=_int(g("DASHBOARD_PORT"), 8765),
            project_root=root,
        )

    # --- Mode scheduling -------------------------------------------------

    def mode_for_time(self, now: Optional[datetime] = None) -> RunMode:
        """Determine the run mode for a given wall-clock time (rule 6).

        PREPARATION starts a few minutes before the expected event; COMPETITION
        covers the expected event ± a window; otherwise NORMAL.
        """
        now = now or datetime.now()
        t = now.time()
        if self._between(t, self.competition_start, self.expected_event_time):
            return RunMode.PREPARATION
        if self._between(t, self.expected_event_time, self.competition_end):
            return RunMode.COMPETITION
        return RunMode.NORMAL

    @staticmethod
    def _between(t: time, start: time, end: time) -> bool:
        if start <= end:
            return start <= t < end
        # wraps midnight
        return t >= start or t < end

    def preparation_lead_seconds(self) -> int:
        """Minutes between competition_start and expected_event_time, in seconds."""
        return self._seconds_between(self.competition_start, self.expected_event_time)

    @staticmethod
    def _seconds_between(a: time, b: time) -> int:
        da = datetime(2000, 1, 1, a.hour, a.minute)
        db = datetime(2000, 1, 1, b.hour, b.minute)
        return int((db - da).total_seconds())

    def ensure_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
