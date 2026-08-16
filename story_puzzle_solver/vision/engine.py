"""Vision engine (rule 26) - secondary, never the sole source of truth.

This module exposes a pluggable interface for an *optional* secondary
confirmation backend. **No backend is configured by default**, so the engine
reports ``available() == False`` and ``status == UNAVAILABLE`` (rule 10). It
must NEVER pretend to have performed analysis when it has not.

When a real backend is wired (e.g. a local ONNX model or a multimodal Vision
API), a subclass or configured provider can override :meth:`recognize_region`.
Until then the pipeline runs on OCR + ImageDiff alone (rule 70).

A purely heuristic, clearly-labelled :meth:`confirm_card` colour check is
provided as an OPTIONAL, non-authoritative helper. It is only used when
explicitly enabled and never counts as a real analysis.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from ..common.logger import JsonLogger


VISION_STATUS_AVAILABLE = "AVAILABLE"
VISION_STATUS_UNAVAILABLE = "UNAVAILABLE"


@dataclass
class VisionResult:
    text: str = ""
    confidence: float = 0.0
    notes: str = ""
    available: bool = False
    status: str = VISION_STATUS_UNAVAILABLE


class VisionEngine:
    def __init__(self, enabled: bool = False, logger: Optional[JsonLogger] = None,
                 provider: Optional[object] = None):
        self.enabled = enabled
        self._provider = provider  # a real backend (None = none configured)
        self._logger = logger or JsonLogger("vision")
        if enabled and provider is None:
            self._logger.info("vision_no_backend", status=VISION_STATUS_UNAVAILABLE,
                              note="VISION_ENABLED=true but no provider configured; running OCR+Diff only")

    def available(self) -> bool:
        """True only when a real backend is actually configured."""
        return self.enabled and self._provider is not None

    def status(self) -> str:
        return VISION_STATUS_AVAILABLE if self.available() else VISION_STATUS_UNAVAILABLE

    def confirm_card(self, normalized_card: Optional[np.ndarray]) -> bool:
        """OPTIONAL non-authoritative heuristic: beige/gold colour presence.

        Never used as proof. Only meaningful when explicitly invoked by the
        pipeline as a cheap sanity check; it does not make Vision "available".
        """
        if normalized_card is None:
            return False
        try:
            hsv = cv2.cvtColor(normalized_card, cv2.COLOR_BGR2HSV)
            gold = cv2.inRange(hsv, np.array([18, 30, 100]), np.array([40, 180, 255]))
            return float(gold.mean() / 255.0) > 0.3
        except Exception:
            return False

    def recognize_region(self, region: Optional[np.ndarray], digit_mode: bool = True) -> VisionResult:
        """If no backend is configured, return UNAVAILABLE - never a fake result."""
        if not self.available():
            return VisionResult(available=False, status=VISION_STATUS_UNAVAILABLE,
                                notes="no_vision_backend_configured")
        # A real provider would OCR here; we do not fake a result.
        return VisionResult(available=False, status=VISION_STATUS_UNAVAILABLE,
                            notes="provider_present_but_not_implemented")
