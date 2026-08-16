"""Vision engine (rule 26) — secondary, never the sole source of truth.

A pluggable stub that can confirm card presence, region positions, visibility,
change interpretation, and OCR. It is OPTIONAL: the system continues with
OCR + ImageDiff if Vision is unavailable (rule 70). A real implementation
could call a multimodal Vision API; here we provide a no-op stub plus a
heuristic fallback so the fusion pipeline has something to fuse when enabled.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from ..common.logger import JsonLogger


@dataclass
class VisionResult:
    text: str = ""
    confidence: float = 0.0
    notes: str = ""
    available: bool = False


class VisionEngine:
    def __init__(self, enabled: bool = False, logger: Optional[JsonLogger] = None):
        self.enabled = enabled
        self._logger = logger or JsonLogger("vision")
        # No external API are configured; Vision is a stub (rule 26, 70).

    def available(self) -> bool:
        return self.enabled

    def confirm_card(self, normalized_card: np.ndarray) -> bool:
        """Heuristic confirmation: card-like colour distribution. Never authoritative."""
        if not self.enabled or normalized_card is None:
            return False
        hsv = cv2.cvtColor(normalized_card, cv2.COLOR_BGR2HSV)
        # beige/gold hue present?
        gold = cv2.inRange(hsv, np.array([18, 30, 100]), np.array([40, 180, 255]))
        return float(gold.mean() / 255.0) > 0.3

    def recognize_region(self, region: np.ndarray, digit_mode: bool = True) -> VisionResult:
        """Stub: returns no text (real Vision would OCR). Never used as sole truth."""
        if not self.enabled:
            return VisionResult(available=False)
        return VisionResult(text="", confidence=0.0, notes="vision_stub", available=True)
