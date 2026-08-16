"""OCR engine: multi-variant with early stopping (rules 23, 24).

For important regions, test multiple preprocessing variants:
  original, grayscale, contrast, sharpened, threshold, adaptive_threshold,
  upscaled_2x, upscaled_4x.

Rule 24: early stop — if a variant yields confidence >= high threshold, don't
run the rest unnecessarily.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

import cv2
import numpy as np

from ..common.logger import JsonLogger
from ..common.timing import Timer, now_ms
from .provider import OCRProvider, OCRResult, TesseractOCRProvider


class DigitMode(str, Enum):
    OFF = "OFF"
    ON = "ON"  # numeric whitelist + segmentation


class OCRVariant(str, Enum):
    ORIGINAL = "original"
    GRAYSCALE = "grayscale"
    CONTRAST = "contrast"
    SHARPENED = "sharpened"
    THRESHOLD = "threshold"
    ADAPTIVE_THRESHOLD = "adaptive_threshold"
    UPSCALED_2X = "upscaled_2x"
    UPSCALED_4X = "upscaled_4x"


def _variant(image: np.ndarray, name: str) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    if name == OCRVariant.ORIGINAL:
        return image
    if name == OCRVariant.GRAYSCALE:
        return gray
    if name == OCRVariant.CONTRAST:
        return cv2.convertScaleAbs(gray, alpha=2.0, beta=-30)
    if name == OCRVariant.SHARPENED:
        kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])
        return cv2.filter2D(gray, -1, kernel)
    if name == OCRVariant.THRESHOLD:
        # dark text on light bg: threshold and invert so text is black on white
        _, t = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        # if the image is mostly light (text is dark), THRESH_BINARY leaves text
        # dark on white which is what tesseract wants. If mostly dark, invert.
        if gray.mean() < 127:
            t = cv2.bitwise_not(t)
        return t
    if name == OCRVariant.ADAPTIVE_THRESHOLD:
        t = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY, 15, 5)
        if gray.mean() < 127:
            t = cv2.bitwise_not(t)
        return t
    if name == OCRVariant.UPSCALED_2X:
        h, w = gray.shape[:2]
        return _pad_and_resize(gray, w * 2, h * 2)
    if name == OCRVariant.UPSCALED_4X:
        h, w = gray.shape[:2]
        return _pad_and_resize(gray, w * 4, h * 4)
    return image


def _pad_and_resize(gray: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    """Add a white border before upscaling so digits aren't clipped, and
    tesseract has margin (psm 7 works best with padding around the text)."""
    border = max(8, target_h // 4)
    padded = cv2.copyMakeBorder(gray, border, border, border, border,
                                cv2.BORDER_CONSTANT, value=255)
    return cv2.resize(padded, (target_w + 2 * border, target_h + 2 * border),
                      interpolation=cv2.INTER_CUBIC)


class OCREngine:
    def __init__(self, primary: Optional[OCRProvider] = None,
                 secondary: Optional[OCRProvider] = None,
                 confidence_high: float = 0.90,
                 confidence_medium: float = 0.70,
                 digit_whitelist: str = "0123456789",
                 logger: Optional[JsonLogger] = None):
        self.primary = primary or TesseractOCRProvider()
        self.secondary = secondary
        self.confidence_high = confidence_high
        self.confidence_medium = confidence_medium
        self.digit_whitelist = digit_whitelist
        self._logger = logger or JsonLogger("ocr")

    def available(self) -> bool:
        return self.primary.available()

    def recognize_region(self, region: np.ndarray, digit_mode: bool = True,
                         early_stop: bool = True,
                         max_variants: Optional[int] = None,
                         deadline_ms: Optional[float] = None) -> OCRResult:
        """Run multi-variant OCR with early stopping (rule 24).

        ``max_variants`` caps how many variants to try (text regions are
        noisy; cap them). ``deadline_ms`` aborts remaining variants once the
        budget is exhausted so one slow region never blocks the batch.
        """
        with Timer() as t:
            start_ms = now_ms()
            best = OCRResult(text="", confidence=0.0, variant="none")
            if not self.primary.available() and (self.secondary is None or not self.secondary.available()):
                self._logger.warn("ocr_no_provider")
                return best
            variants = [
                OCRVariant.UPSCALED_4X, OCRVariant.UPSCALED_2X,
                OCRVariant.ADAPTIVE_THRESHOLD, OCRVariant.THRESHOLD,
                OCRVariant.SHARPENED, OCRVariant.CONTRAST,
                OCRVariant.GRAYSCALE, OCRVariant.ORIGINAL,
            ]
            if max_variants:
                variants = variants[:max_variants]
            wl = self.digit_whitelist if digit_mode else None
            for v in variants:
                if deadline_ms and (now_ms() - start_ms) > deadline_ms:
                    break
                img = _variant(region, v.value)
                if img.size == 0:
                    continue
                res = self._run_provider(img, digit_mode, wl)
                res.variant = v.value
                # rule 2: reject zero-confidence / empty results as candidates
                if res.text and res.confidence > 0:
                    if (res.confidence > best.confidence or
                            (res.confidence == best.confidence and len(res.text) > len(best.text))):
                        best = res
                if early_stop and best.confidence >= self.confidence_high and best.text:
                    break
            # rule 2: never invent. If text has non-whitelist chars in digit mode, mark unknown.
            if digit_mode and best.text:
                best = self._enforce_whitelist(best, wl)
        self._logger.info("ocr_done", variant=best.variant,
                         confidence=round(best.confidence, 3),
                         latency_ms=round(t.elapsed_ms, 2))
        return best

    def _run_provider(self, img: np.ndarray, digit_mode: bool, wl: Optional[str]) -> OCRResult:
        try:
            res = self.primary.recognize(img, digit_mode=digit_mode, whitelist=wl)
            if res.text and res.confidence >= self.confidence_medium:
                return res
            # try secondary if primary is weak
            if self.secondary is not None and self.secondary.available():
                res2 = self.secondary.recognize(img, digit_mode=digit_mode, whitelist=wl)
                if res2.confidence > res.confidence:
                    return res2
            return res
        except Exception as e:
            self._logger.warn("ocr_provider_error", error=str(e))
            return OCRResult(text="", confidence=0.0, variant="error")

    def _enforce_whitelist(self, res: OCRResult, wl: Optional[str]) -> OCRResult:
        if not wl:
            return res
        cleaned = "".join(c for c in res.text if c in wl)
        if cleaned != res.text:
            # partial: keep only valid digits, lower confidence (rule 2)
            res.text = cleaned
            res.confidence *= 0.6
        return res
