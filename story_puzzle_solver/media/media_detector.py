"""Media type classification and deduplication (rules 8, 9).

Each media is identified by:
  - story_id / media_id
  - content_hash  (sha256 of bytes — exact match)
  - perceptual_hash (image: pHash via DCT; video: pHash of a representative frame)

If a media is already known -> CACHE HIT, skip full analysis (rule 8).
"""
from __future__ import annotations

import hashlib
import struct
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

import cv2
import numpy as np

from ..common.logger import JsonLogger
from ..common.timing import Timer


class MediaKind(str, Enum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    UNKNOWN = "UNKNOWN"


@dataclass
class MediaInfo:
    media_id: str
    path: Path
    kind: MediaKind
    content_hash: str
    perceptual_hash: str
    width: int = 0
    height: int = 0
    duration_s: float = 0.0
    frame_count: int = 0


@dataclass
class DedupResult:
    is_known: bool
    media: Optional[MediaInfo] = None


class DedupStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_content: Dict[str, MediaInfo] = {}
        self._by_perceptual: Dict[str, MediaInfo] = {}

    def check(self, content_hash: str, perceptual_hash: str) -> DedupResult:
        with self._lock:
            if content_hash in self._by_content:
                return DedupResult(is_known=True, media=self._by_content[content_hash])
            if perceptual_hash in self._by_perceptual:
                return DedupResult(is_known=True, media=self._by_perceptual[perceptual_hash])
        return DedupResult(is_known=False)

    def remember(self, media: MediaInfo) -> None:
        with self._lock:
            self._by_content[media.content_hash] = media
            self._by_perceptual[media.perceptual_hash] = media

    def clear(self) -> None:
        with self._lock:
            self._by_content.clear()
            self._by_perceptual.clear()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _phash_image(img: np.ndarray, hash_size: int = 16) -> str:
    """DCT-based perceptual hash (compact, robust to compression/scaling)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    small = cv2.resize(gray, (hash_size * 4, hash_size * 4), interpolation=cv2.INTER_AREA)
    small = small.astype(np.float32)
    dct = cv2.dct(small)
    low = dct[:hash_size, :hash_size]
    med = np.median(low)
    bits = (low > med).flatten()
    # pack to hex
    packed = bytearray()
    for b in bits:
        packed.append(49 if b else 48)
    return packed.decode()


def _phash_video(path: Path, hash_size: int = 16) -> str:
    """Perceptual hash of the middle frame of a video."""
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    target = max(0, n // 2)
    cap.set(cv2.CAP_PROP_POS_FRAMES, target)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return "v0000"
    return "v" + _phash_image(frame, hash_size)


class MediaDetector:
    def __init__(self, logger: Optional[JsonLogger] = None, dedup: Optional[DedupStore] = None):
        self._logger = logger or JsonLogger("media")
        self.dedup = dedup or DedupStore()

    def classify(self, path: Path) -> MediaKind:
        """Detect IMAGE vs VIDEO vs UNKNOWN (rule 9)."""
        p = str(path)
        # try image first
        img = cv2.imread(p, cv2.IMREAD_COLOR)
        if img is not None and img.size > 0:
            return MediaKind.IMAGE
        cap = cv2.VideoCapture(p)
        ok, frame = cap.read()
        cap.release()
        if ok and frame is not None:
            return MediaKind.VIDEO
        # fallback on extension
        ext = path.suffix.lower()
        if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            return MediaKind.IMAGE
        if ext in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
            return MediaKind.VIDEO
        return MediaKind.UNKNOWN

    def analyze(self, path: Path, story_id: str) -> MediaInfo:
        with Timer() as t:
            kind = self.classify(path)
            content_hash = _sha256(path)
            width = height = 0
            duration_s = 0.0
            frame_count = 0
            if kind == MediaKind.IMAGE:
                img = cv2.imread(str(path))
                if img is not None:
                    h, w = img.shape[:2]
                    width, height = w, h
                    perceptual_hash = _phash_image(img)
                else:
                    perceptual_hash = "i0000"
            elif kind == MediaKind.VIDEO:
                cap = cv2.VideoCapture(str(path))
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                fps = cap.get(cv2.CAP_PROP_FPS) or 0
                if fps and frame_count:
                    duration_s = frame_count / fps
                cap.release()
                perceptual_hash = _phash_video(path)
            else:
                perceptual_hash = "u0000"
            media_id = f"{story_id}::{content_hash[:12]}"
            info = MediaInfo(
                media_id=media_id, path=path, kind=kind,
                content_hash=content_hash, perceptual_hash=perceptual_hash,
                width=width, height=height, duration_s=duration_s, frame_count=frame_count,
            )
        self._logger.info("media_analyzed", kind=kind.value, latency_ms=round(t.elapsed_ms, 2))
        return info

    def check_dedup(self, info: MediaInfo) -> DedupResult:
        res = self.dedup.check(info.content_hash, info.perceptual_hash)
        if res.is_known:
            self._logger.info("cache_hit", perceptual=info.perceptual_hash[:16])
        return res

    def remember(self, info: MediaInfo) -> None:
        self.dedup.remember(info)
