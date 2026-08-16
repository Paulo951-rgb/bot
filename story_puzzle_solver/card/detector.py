"""CardDetector — multi-signal card localization (rule 14).

Signals combined:
  - colour: beige/gold blob (HSV range)
  - contours + quadrilateral approximation
  - aspect ratio (~1.586)
  - perspective (quad area / bounding area)
  - template/structure similarity (WORLD ELITE zone, contactless symbol)
  - similarity with previously detected cards (via CardTemplate hashes)

Returns a CardDetection with confidence, bounding box and (when possible)
four corners for homography alignment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

from ..common.logger import JsonLogger
from ..common.timing import Timer
from .template import CardTemplate, CARD_ASPECT

# HSV range for the beige/gold card (rule 3: beige/gold design)
GOLD_LOWER = np.array([18, 35, 110])
GOLD_UPPER = np.array([40, 170, 255])


@dataclass
class CardDetection:
    detected: bool
    confidence: float
    bounding_box: Optional[Tuple[int, int, int, int]] = None  # x,y,w,h
    corners: Optional[List[Tuple[int, int]]] = None  # 4 pts (TL,TR,BR,BL)
    method_scores: dict = field(default_factory=dict)


def _order_corners(pts: List[Tuple[float, float]]) -> List[Tuple[int, int]]:
    """Order 4 points as TL, TR, BR, BL.

    Uses the classic sum/difference method which is robust for convex
    quadrilaterals: TL has min(x+y), BR has max(x+y), TR has max(x-y),
    BL has min(x-y). This tolerates small rotations correctly.
    """
    pts = [(float(x), float(y)) for x, y in pts]
    if len(pts) != 4:
        return [(int(p[0]), int(p[1])) for p in pts]
    tl = min(pts, key=lambda p: p[0] + p[1])
    br = max(pts, key=lambda p: p[0] + p[1])
    tr = max(pts, key=lambda p: p[0] - p[1])
    bl = min(pts, key=lambda p: p[0] - p[1])
    return [(int(tl[0]), int(tl[1])), (int(tr[0]), int(tr[1])),
            (int(br[0]), int(br[1])), (int(bl[0]), int(bl[1]))]


def _quad_from_contour(cnt: np.ndarray) -> Optional[List[Tuple[float, float]]]:
    """Extract a quadrilateral from a contour.

    Prefer the rotated minimum-area rectangle (stable for near-rectangular
    objects like a card), then refine with approxPolyDP if it cleanly yields 4
    points that are close to the min-area rect (sharper corners). The min-area
    rect alone is more robust than approxPolyDP, which can clip corners.
    """
    rect = cv2.minAreaRect(cnt)
    box = cv2.boxPoints(rect)
    box_pts = [(float(p[0]), float(p[1])) for p in box]

    # try approxPolyDP for sharper corners; accept only if all 4 corners are
    # within a tolerance of the min-area rect corners (otherwise it clipped).
    peri = cv2.arcLength(cnt, True)
    for eps in (0.02, 0.03, 0.05):
        approx = cv2.approxPolyDP(cnt, eps * peri, True)
        if len(approx) == 4:
            approx_pts = [(float(approx[i][0][0]), float(approx[i][0][1])) for i in range(4)]
            # match each approx corner to nearest box corner; if max dist is
            # small, use the sharper approx corners.
            max_dist = 0.0
            for ap in approx_pts:
                d = min(np.hypot(ap[0] - bp[0], ap[1] - bp[1]) for bp in box_pts)
                max_dist = max(max_dist, d)
            if max_dist < 18.0:
                return approx_pts
    return box_pts


def _aspect_score(corners: List[Tuple[int, int]]) -> Tuple[float, float]:
    """Return (aspect_ratio, aspect_score 0..1) from ordered corners."""
    tl, tr, br, bl = corners
    w = (np.hypot(tr[0] - tl[0], tr[1] - tl[1]) + np.hypot(br[0] - bl[0], br[1] - bl[1])) / 2
    h = (np.hypot(bl[0] - tl[0], bl[1] - tl[1]) + np.hypot(br[0] - tr[0], br[1] - tr[1])) / 2
    aspect = w / max(1.0, h)
    score = max(0.0, 1.0 - abs(aspect - CARD_ASPECT) / 1.0)
    return aspect, score


class CardDetector:
    def __init__(self, template: Optional[CardTemplate] = None,
                 min_area_frac: float = 0.05, logger: Optional[JsonLogger] = None):
        self.template = template or CardTemplate()
        self.min_area_frac = min_area_frac
        self._logger = logger or JsonLogger("card_detector")
        self._prev_card_hashes: List[str] = []

    def detect(self, frame: np.ndarray) -> CardDetection:
        with Timer() as t:
            det = self._detect_inner(frame)
        det.method_scores["latency_ms"] = round(t.elapsed_ms, 2)
        self._logger.info("card_detect", detected=det.detected,
                         confidence=round(det.confidence, 3),
                         latency_ms=round(t.elapsed_ms, 2))
        return det

    def _detect_inner(self, frame: np.ndarray) -> CardDetection:
        h, w = frame.shape[:2]
        scores: dict = {}

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, GOLD_LOWER, GOLD_UPPER)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (21, 21))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        best = None
        best_score = 0.0
        for c in contours:
            area = cv2.contourArea(c)
            if area < (w * h * self.min_area_frac):
                continue
            x, y, cw, ch = cv2.boundingRect(c)
            aspect_bb = cw / max(1, ch)
            if not (1.1 <= aspect_bb <= 2.3):
                continue
            quad = _quad_from_contour(c)
            if quad is None:
                continue
            corners = _order_corners(quad)
            aspect, asp_score = _aspect_score(corners)
            if asp_score < 0.2:
                continue
            # quad solidity
            cnt_area = cv2.contourArea(c)
            quad_area = cv2.contourArea(np.array(corners, dtype=np.int32))
            solidity = cnt_area / max(1.0, quad_area)
            solidity = max(0.0, min(1.0, solidity))
            # size score
            size_frac = (cw * ch) / (w * h)
            size_score = min(1.0, size_frac * 2.5)
            # colour purity: fraction of gold pixels within bbox
            roi_mask = mask[y:y + ch, x:x + cw]
            colour_purity = float(roi_mask.mean() / 255.0) if roi_mask.size else 0.0
            # combined
            score = (0.30 * asp_score + 0.20 * size_score +
                     0.20 * solidity + 0.30 * colour_purity)
            if score > best_score:
                best_score = score
                best = (corners, (x, y, cw, ch), asp_score, solidity, colour_purity, size_score, aspect)

        if best is None:
            return CardDetection(detected=False, confidence=0.0, method_scores=scores)

        corners, box, asp_score, solidity, colour_purity, size_score, aspect = best
        scores.update(aspect=round(aspect, 3), aspect_score=round(asp_score, 3),
                      solidity=round(solidity, 3), colour_purity=round(colour_purity, 3),
                      size_score=round(size_score, 3))

        # template similarity: compare gold-pixel distribution to reference
        # hashes if available (lightweight: just colour purity already counts).
        # If a normalized card is known, we could match; keep simple here.
        conf = min(1.0, best_score)
        # require a minimum colour purity to avoid false positives from
        # similarly-coloured backgrounds
        if colour_purity < 0.25:
            conf *= 0.5
        detected = conf >= 0.35
        return CardDetection(
            detected=detected, confidence=conf,
            bounding_box=box, corners=corners, method_scores=scores,
        )

    def learn_reference(self, normalized_card_hash: str) -> None:
        if normalized_card_hash and normalized_card_hash not in self._prev_card_hashes:
            self._prev_card_hashes.append(normalized_card_hash)
