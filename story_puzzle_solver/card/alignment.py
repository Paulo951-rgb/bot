"""Card alignment via homography to a normalized card (rule 16).

CARD -> CORNERS -> HOMOGRAPHY -> NORMALIZED CARD (default 1024 x 640).
Resolution is configurable (rule 16). If corners are unavailable, fall back to
the axis-aligned bounding box (degraded but functional).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

from ..common.logger import JsonLogger
from ..common.timing import Timer
from .detector import CardDetection, _order_corners


@dataclass
class NormalizedCard:
    image: np.ndarray
    width: int
    height: int
    corners_used: List[Tuple[int, int]]
    method: str  # "homography" | "bbox"
    success: bool


class CardAligner:
    def __init__(self, width: int = 1024, height: int = 640,
                 logger: Optional[JsonLogger] = None):
        self.width = width
        self.height = height
        self._logger = logger or JsonLogger("aligner")

    def align(self, frame: np.ndarray, detection: CardDetection) -> Optional[NormalizedCard]:
        with Timer() as t:
            if not detection.detected:
                return None
            if detection.corners and len(detection.corners) == 4:
                warped = self._warp_from_corners(frame, detection.corners)
                if warped is not None:
                    self._logger.info("align", method="homography",
                                     latency_ms=round(t.elapsed_ms, 2))
                    return NormalizedCard(warped, self.width, self.height,
                                         list(detection.corners), "homography", True)
            # fallback: bbox
            if detection.bounding_box:
                x, y, w, h = detection.bounding_box
                roi = frame[y:y + h, x:x + w]
                if roi.size:
                    warped = cv2.resize(roi, (self.width, self.height), interpolation=cv2.INTER_AREA)
                    self._logger.info("align", method="bbox",
                                     latency_ms=round(t.elapsed_ms, 2))
                    return NormalizedCard(warped, self.width, self.height,
                                         [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                                         "bbox", True)
        return None

    def _warp_from_corners(self, frame: np.ndarray,
                           corners: List[Tuple[int, int]]) -> Optional[np.ndarray]:
        src = np.float32(corners)
        dst = np.float32([[0, 0], [self.width, 0],
                          [self.width, self.height], [0, self.height]])
        H, _ = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if H is None:
            # try without RANSAC (exactly 4 points)
            H, _ = cv2.findHomography(src, dst)
        if H is None:
            return None
        return cv2.warpPerspective(frame, H, (self.width, self.height),
                                   flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_REPLICATE)

    def extract_region(self, normalized: NormalizedCard,
                       rect_rel: Tuple[float, float, float, float]) -> np.ndarray:
        x = int(rect_rel[0] * normalized.width)
        y = int(rect_rel[1] * normalized.height)
        w = int(rect_rel[2] * normalized.width)
        h = int(rect_rel[3] * normalized.height)
        x = max(0, min(x, normalized.width - 1))
        y = max(0, min(y, normalized.height - 1))
        w = max(1, min(w, normalized.width - x))
        h = max(1, min(h, normalized.height - y))
        return normalized.image[y:y + h, x:x + w].copy()
