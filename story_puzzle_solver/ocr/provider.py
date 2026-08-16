"""OCR provider abstraction (rule 22).

``OCRProvider`` is the interface. ``TesseractOCRProvider`` is the local
provider (available in this environment). Other providers (PaddleOCR, EasyOCR,
ONNX OCR) can be added; the benchmark should pick the best for small-character
precision + latency (rule 22). The interface stays stable.
"""
from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

import cv2
import numpy as np


@dataclass
class OCRResult:
    text: str
    confidence: float
    variant: str = ""
    boxes: List[tuple] = field(default_factory=list)  # per-char boxes if available


class OCRProvider(ABC):
    name: str = "base"

    @abstractmethod
    def recognize(self, image: np.ndarray, digit_mode: bool = False,
                  whitelist: Optional[str] = None) -> OCRResult: ...

    def available(self) -> bool:
        return True


class TesseractOCRProvider(OCRProvider):
    """Local Tesseract via pytesseract."""

    name = "tesseract"

    def __init__(self, lang: str = "eng"):
        import os
        # Tesseract spawns internal OpenMP threads; when several OCR calls run
        # concurrently they thrash the CPU. Pin tesseract to a single thread so
        # our worker pool provides real parallelism without contention.
        os.environ.setdefault("OMP_THREAD_LIMIT", "1")
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        import pytesseract
        self._pytesseract = pytesseract
        self.lang = lang
        self._available = shutil.which("tesseract") is not None

    def available(self) -> bool:
        return self._available

    def recognize(self, image: np.ndarray, digit_mode: bool = False,
                  whitelist: Optional[str] = None) -> OCRResult:
        if not self._available:
            return OCRResult(text="", confidence=0.0, variant="tesseract_unavailable")
        img = image
        if img.ndim == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # tesseract config
        if digit_mode:
            psm = "7"  # single text line
            wl = whitelist or "0123456789"
            config = f"--psm {psm} -c tessedit_char_whitelist={wl}"
        else:
            psm = "7"
            config = f"--psm {psm}"
        try:
            data = self._pytesseract.image_to_data(
                img, lang=self.lang, config=config,
                output_type=self._pytesseract.Output.DICT,
            )
        except Exception:
            return OCRResult(text="", confidence=0.0, variant="tesseract_error")
        texts = data.get("text", [])
        confs = data.get("conf", [])
        words = [t.strip() for t in texts if t and t.strip()]
        conf_vals = [float(c) for c, t in zip(confs, texts) if t and t.strip() and str(c) != "-1"]
        text = "".join(words).strip()
        conf = (sum(conf_vals) / len(conf_vals) / 100.0) if conf_vals else 0.0
        boxes = []
        for i, t in enumerate(texts):
            if t and t.strip():
                boxes.append((data["left"][i], data["top"][i],
                              data["width"][i], data["height"][i]))
        return OCRResult(text=text, confidence=conf, variant="tesseract", boxes=boxes)
