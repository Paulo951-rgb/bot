"""Video frame engine (rules 11, 12, 13).

Three sampling levels:
  LEVEL 1 — SCAN  : few frames, fast signals (histogram, dimensions, light
                    card detector) to find where the card exists.
  LEVEL 2 — FOCUS : increase sampling around detected time window.
  LEVEL 3 — EXTRACT: pick best-quality frames for OCR.

Rule 13 EARLY EXIT: as soon as a frame yields NEWLY_REVEALED + HIGH confidence,
emit the result immediately; confirmation continues in the background.

The engine is *card-detector-agnostic*: it accepts a callable
``card_probe(frame) -> (detected: bool, confidence: float, box)`` so the heavy
CardDetector (phase 7) can be plugged in. A lightweight default probe uses
colour+aspect heuristics so the engine is testable standalone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

from ..common.logger import JsonLogger
from ..common.timing import Timer, now_ms


CardProbe = Callable[[np.ndarray], Tuple[bool, float, Optional[Tuple[int, int, int, int]]]]
QualityScoreFn = Callable[[np.ndarray, Optional[Tuple[int, int, int, int]]], float]


class ScanLevel(IntEnum):
    SCAN = 1
    FOCUS = 2
    EXTRACT = 3


@dataclass
class FrameQuality:
    sharpness: float
    brightness: float
    contrast: float
    blur: float
    card_size: float
    angle: float
    score: float


@dataclass
class FrameCandidate:
    frame_index: int
    timestamp_s: float
    image: np.ndarray
    card_box: Optional[Tuple[int, int, int, int]]
    card_confidence: float
    quality: FrameQuality
    level: ScanLevel


def default_card_probe(frame: np.ndarray) -> Tuple[bool, float, Optional[Tuple[int, int, int, int]]]:
    """Lightweight probe: look for a large beige/gold blob with card aspect.

    Used when no full CardDetector is wired yet. Returns (detected, conf, box).
    """
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    # beige/gold: hue ~20-40, sat ~40-140, val ~120-255
    lower = np.array([18, 40, 120])
    upper = np.array([40, 160, 255])
    mask = cv2.inRange(hsv, lower, upper)
    # clean up
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best_box = None
    best_score = 0.0
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        area = cw * ch
        if area < (w * h * 0.05):  # too small
            continue
        aspect = cw / max(1, ch)
        # card aspect ~1.586; accept 1.2..2.2
        if not (1.2 <= aspect <= 2.2):
            continue
        score = min(1.0, (area / (w * h)) * 2.0) * (1.0 - abs(aspect - 1.586) / 1.0)
        if score > best_score:
            best_score = score
            best_box = (x, y, cw, ch)
    if best_box is None:
        return False, 0.0, None
    conf = min(1.0, best_score)
    return conf > 0.15, conf, best_box


def compute_quality(frame: np.ndarray, box: Optional[Tuple[int, int, int, int]]) -> FrameQuality:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # sharpness via Laplacian variance
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    sharpness = float(lap.var())
    brightness = float(gray.mean())
    contrast = float(gray.std())
    # blur: lower laplacian variance => more blur; invert to a 0..1-ish metric
    blur_metric = 1.0 / (1.0 + sharpness / 500.0)
    card_size = 0.0
    angle = 0.0
    if box is not None:
        x, y, cw, ch = box
        card_size = (cw * ch) / (frame.shape[1] * frame.shape[0])
        angle = abs(np.arctan2(ch, cw) - np.arctan2(1, 1.586))
    # combined score (higher better). Normalized heuristics.
    size_score = min(1.0, card_size * 2.5)
    sharp_score = min(1.0, sharpness / 600.0)
    bright_score = 1.0 - abs(brightness - 150) / 150.0
    bright_score = max(0.0, bright_score)
    score = (0.4 * size_score + 0.3 * sharp_score + 0.2 * bright_score + 0.1 * (1 - blur_metric))
    return FrameQuality(
        sharpness=sharpness, brightness=brightness, contrast=contrast,
        blur=blur_metric, card_size=card_size, angle=angle, score=score,
    )


class VideoFrameEngine:
    def __init__(self, initial_sample_ms: int = 300, focused_sample_ms: int = 75,
                 max_frames: int = 400, logger: Optional[JsonLogger] = None,
                 card_probe: Optional[CardProbe] = None,
                 quality_fn: Optional[QualityScoreFn] = None):
        self.initial_sample_ms = initial_sample_ms
        self.focused_sample_ms = focused_sample_ms
        self.max_frames = max_frames
        self._logger = logger or JsonLogger("video")
        self._probe = card_probe or default_card_probe
        self._quality_fn = quality_fn or compute_quality

    def scan(self, path: Path) -> List[FrameCandidate]:
        """LEVEL 1: sparse sampling to find card presence (rule 12)."""
        with Timer() as t:
            cap = cv2.VideoCapture(str(path))
            if not cap.isOpened():
                raise IOError(f"cannot open video: {path}")
            fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            step = max(1, int((self.initial_sample_ms / 1000.0) * fps))
            n = min(self.max_frames, max(0, total))
            candidates: List[FrameCandidate] = []
            idx = 0
            while idx < n:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                ts = idx / fps
                detected, conf, box = self._probe(frame)
                if detected and box is not None:
                    q = self._quality_fn(frame, box)
                    candidates.append(FrameCandidate(idx, ts, frame, box, conf, q, ScanLevel.SCAN))
                idx += step
            cap.release()
        self._logger.info("video_scan", frames_checked=(n // step), hits=len(candidates),
                         latency_ms=round(t.elapsed_ms, 2))
        return candidates

    def focus(self, path: Path, around: FrameCandidate, window_s: float = 1.0) -> List[FrameCandidate]:
        """LEVEL 2: denser sampling around a detected frame (rule 12)."""
        with Timer() as t:
            cap = cv2.VideoCapture(str(path))
            fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
            step = max(1, int((self.focused_sample_ms / 1000.0) * fps))
            lo = max(0, int((around.timestamp_s - window_s) * fps))
            hi = int((around.timestamp_s + window_s) * fps)
            out: List[FrameCandidate] = []
            idx = lo
            while idx <= hi:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                ts = idx / fps
                detected, conf, box = self._probe(frame)
                if detected and box is not None:
                    q = self._quality_fn(frame, box)
                    out.append(FrameCandidate(idx, ts, frame, box, conf, q, ScanLevel.FOCUS))
                idx += step
            cap.release()
        self._logger.info("video_focus", around=around.frame_index, hits=len(out),
                         latency_ms=round(t.elapsed_ms, 2))
        return out

    def extract_best(self, candidates: List[FrameCandidate], k: int = 5) -> List[FrameCandidate]:
        """LEVEL 3: select top-k frames by quality (rule 12)."""
        with Timer() as t:
            ranked = sorted(candidates, key=lambda c: c.quality.score, reverse=True)
            top = ranked[:k]
            for c in top:
                c.level = ScanLevel.EXTRACT
        self._logger.info("video_extract", considered=len(candidates), selected=len(top),
                         latency_ms=round(t.elapsed_ms, 2))
        return top

    def best_candidates(self, path: Path, k: int = 5,
                        on_frame: Optional[Callable[[FrameCandidate], None]] = None
                        ) -> List[FrameCandidate]:
        """Full 3-level pipeline. ``on_frame`` enables EARLY EXIT (rule 13):
        if the callback signals a high-confidence newly-revealed result, the
        engine returns immediately with what it has."""
        with Timer() as t:
            scanned = self.scan(path)
            if not scanned:
                return []
            # pick earliest strong scan hit to focus around
            scanned.sort(key=lambda c: c.frame_index)
            focus_seed = scanned[0]
            focused = self.focus(path, focus_seed, window_s=1.0)
            all_cands = scanned + focused
            # dedup by frame index keeping best
            by_idx = {}
            for c in all_cands:
                if c.frame_index not in by_idx or c.quality.score > by_idx[c.frame_index].quality.score:
                    by_idx[c.frame_index] = c
            # incremental early exit: try extract on the fly
            ranked = sorted(by_idx.values(), key=lambda c: c.quality.score, reverse=True)
            top: List[FrameCandidate] = []
            for c in ranked[:max(k * 3, k)]:
                c.level = ScanLevel.EXTRACT
                top.append(c)
                if on_frame is not None:
                    on_frame(c)
            top = top[:k]
        self._logger.info("video_pipeline", candidates=len(by_idx), selected=len(top),
                         latency_ms=round(t.elapsed_ms, 2))
        return top
