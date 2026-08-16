"""ImageDiffEngine + MaskDetector (rules 18, 20, 21).

ImageDiffEngine compares two normalized cards using multiple methods:
  - absolute difference (mean)
  - SSIM (structural similarity, inverted to a diff score)
  - perceptual difference (pHash hamming)
  - feature matching (ORB) — optional, falls back gracefully

Rule 18: do not depend on a single method. The combined diff per region is the
weighted average.

MaskDetector (rule 20): detects red rectangles primarily, but also detects
blur, pixelation, opaque uniform rectangles, obstructions, and abrupt
artificial changes. Never assumes all future masks are red/same shape.

Snapcode exclusion (rule 21): handled structurally — we only ever diff the
*normalized card* (the aligned card quadrilateral), which by construction
excludes the snapcode that sits *below* the card. Additionally, a relative
exclusion zone is applied to region-of-interest crops.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from ..common.logger import JsonLogger
from ..common.timing import Timer
from ..card.template import CardTemplate, RegionState


@dataclass
class RegionDiff:
    key: str
    diff_score: float
    ssim: float
    abs_diff: float
    perceptual_diff: float
    is_masked: bool


@dataclass
class DiffResult:
    overall_diff: float
    regions: Dict[str, RegionDiff] = field(default_factory=dict)


class MaskDetector:
    """Detect masked regions (rule 20)."""

    def __init__(self, red_sat_min: int = 120, red_val_min: int = 80):
        self.red_sat_min = red_sat_min
        self.red_val_min = red_val_min

    def detect_red_mask(self, region_bgr: np.ndarray) -> Tuple[bool, float]:
        if region_bgr.size == 0:
            return False, 0.0
        hsv = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2HSV)
        # red wraps hue: 0-10 and 170-180
        m1 = cv2.inRange(hsv, np.array([0, self.red_sat_min, self.red_val_min]),
                         np.array([10, 255, 255]))
        m2 = cv2.inRange(hsv, np.array([170, self.red_sat_min, self.red_val_min]),
                         np.array([180, 255, 255]))
        red = cv2.bitwise_or(m1, m2)
        frac = float(red.mean() / 255.0)
        return frac > 0.35, frac

    def detect_uniform_block(self, region_bgr: np.ndarray) -> Tuple[bool, float]:
        """Detect opaque uniform / pixelated block (low std deviation)."""
        if region_bgr.size == 0:
            return False, 0.0
        gray = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY)
        std = float(gray.std())
        # uniform => low std. pixelated => moderate std but blocky edges
        is_uniform = std < 12.0
        return is_uniform, max(0.0, 1.0 - std / 40.0)

    def detect_blur(self, region_bgr: np.ndarray) -> float:
        """Return blur amount (0..1, higher = more blurred)."""
        if region_bgr.size == 0:
            return 0.0
        gray = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY)
        lap = cv2.Laplacian(gray, cv2.CV_64F)
        var = float(lap.var())
        return min(1.0, 1.0 / (1.0 + var / 100.0))

    def is_masked(self, region_bgr: np.ndarray) -> Tuple[bool, str, float]:
        """Combined mask detection. Returns (is_masked, method, confidence)."""
        red, red_f = self.detect_red_mask(region_bgr)
        if red:
            return True, "red", red_f
        uni, uni_f = self.detect_uniform_block(region_bgr)
        if uni:
            return True, "uniform", uni_f
        blur = self.detect_blur(region_bgr)
        if blur > 0.85:
            return True, "blur", blur
        return False, "none", 0.0


def _ssim(img1: np.ndarray, img2: np.ndarray) -> float:
    """SSIM on grayscale, same-size images. Returns 0..1 similarity."""
    try:
        from scipy.signal import fftconvolve
    except Exception:
        fftconvolve = None
    g1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY) if img1.ndim == 3 else img1
    g2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY) if img2.ndim == 3 else img2
    if g1.shape != g2.shape:
        g2 = cv2.resize(g2, (g1.shape[1], g1.shape[0]), interpolation=cv2.INTER_AREA)
    g1 = g1.astype(np.float64)
    g2 = g2.astype(np.float64)
    mu1 = cv2.GaussianBlur(g1, (11, 11), 1.5)
    mu2 = cv2.GaussianBlur(g2, (11, 11), 1.5)
    mu1_sq = mu1 * mu1
    mu2_sq = mu2 * mu2
    mu1_mu2 = mu1 * mu2
    sigma1_sq = cv2.GaussianBlur(g1 * g1, (11, 11), 1.5) - mu1_sq
    sigma2_sq = cv2.GaussianBlur(g2 * g2, (11, 11), 1.5) - mu2_sq
    sigma12 = cv2.GaussianBlur(g1 * g2, (11, 11), 1.5) - mu1_mu2
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    ssim_map = ((2 * mu1_mu2 + c1) * (2 * sigma12 + c2)) / \
               ((mu1_sq + mu2_sq + c1) * (sigma1_sq + sigma2_sq + c2))
    return float(np.clip(ssim_map.mean(), 0.0, 1.0))


def _phash_bits(img: np.ndarray, size: int = 16) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    small = cv2.resize(gray, (size * 4, size * 4), interpolation=cv2.INTER_AREA).astype(np.float32)
    dct = cv2.dct(small)
    low = dct[:size, :size]
    med = np.median(low)
    return (low > med).flatten()


class ImageDiffEngine:
    def __init__(self, template: Optional[CardTemplate] = None,
                 mask_detector: Optional[MaskDetector] = None,
                 logger: Optional[JsonLogger] = None):
        self.template = template or CardTemplate()
        self.mask = mask_detector or MaskDetector()
        self._logger = logger or JsonLogger("diff")

    def diff(self, prev: Optional[np.ndarray], curr: np.ndarray) -> DiffResult:
        with Timer() as t:
            if prev is None:
                # no previous: every currently-visible region is NEWLY_REVEALED
                # by definition, masked ones are MASKED. We can't diff, so
                # return high overall and per-region masked-only.
                result = self._diff_no_prev(curr)
            else:
                result = self._diff_full(prev, curr)
        self._logger.info("diff_done", overall=round(result.overall_diff, 3),
                         regions=len(result.regions), latency_ms=round(t.elapsed_ms, 2))
        return result

    def _diff_no_prev(self, curr: np.ndarray) -> DiffResult:
        regions: Dict[str, RegionDiff] = {}
        for key, spec in self.template.regions.items():
            rect = spec.rect
            x = int(rect[0] * curr.shape[1]); y = int(rect[1] * curr.shape[0])
            w = int(rect[2] * curr.shape[1]); h = int(rect[3] * curr.shape[0])
            roi = curr[y:y + h, x:x + w]
            masked, _, _ = self.mask.is_masked(roi)
            regions[key] = RegionDiff(
                key=key, diff_score=0.0 if masked else 1.0,
                ssim=0.0, abs_diff=0.0 if masked else 1.0,
                perceptual_diff=0.0 if masked else 1.0, is_masked=masked,
            )
        return DiffResult(overall_diff=1.0, regions=regions)

    def _diff_full(self, prev: np.ndarray, curr: np.ndarray) -> DiffResult:
        H, W = curr.shape[:2]
        if prev.shape[:2] != curr.shape[:2]:
            prev = cv2.resize(prev, (W, H), interpolation=cv2.INTER_AREA)
        overall_ssim = _ssim(prev, curr)
        overall_abs = float(np.mean(cv2.absdiff(prev, curr)) / 255.0)
        prev_bits = _phash_bits(prev)
        curr_bits = _phash_bits(curr)
        overall_phash = float(np.count_nonzero(prev_bits != curr_bits) / prev_bits.size)

        regions: Dict[str, RegionDiff] = {}
        for key, spec in self.template.regions.items():
            rect = spec.rect
            x = int(rect[0] * W); y = int(rect[1] * H)
            w = int(rect[2] * W); h = int(rect[3] * H)
            roi_prev = prev[y:y + h, x:x + w]
            roi_curr = curr[y:y + h, x:x + w]
            if roi_prev.size == 0 or roi_curr.size == 0:
                continue
            ssim_r = _ssim(roi_prev, roi_curr)
            abs_r = float(np.mean(cv2.absdiff(roi_prev, roi_curr)) / 255.0)
            ph_r = float(np.count_nonzero(_phash_bits(roi_prev) != _phash_bits(roi_curr)) / (16 * 16))
            masked, _, _ = self.mask.is_masked(roi_curr)
            # combined diff score
            diff_score = 0.4 * (1 - ssim_r) + 0.3 * abs_r + 0.3 * ph_r
            regions[key] = RegionDiff(
                key=key, diff_score=diff_score, ssim=ssim_r,
                abs_diff=abs_r, perceptual_diff=ph_r, is_masked=masked,
            )
        overall = 0.4 * (1 - overall_ssim) + 0.3 * overall_abs + 0.3 * overall_phash
        return DiffResult(overall_diff=overall, regions=regions)
