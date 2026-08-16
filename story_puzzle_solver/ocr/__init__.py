"""OCR engine: provider abstraction, digit mode, multi-variant, temporal (rules 22-25)."""
from .provider import OCRProvider, OCRResult, TesseractOCRProvider
from .engine import OCREngine, DigitMode, OCRVariant
from .temporal import TemporalOCR, TemporalObservation

__all__ = [
    "OCRProvider", "OCRResult", "TesseractOCRProvider",
    "OCREngine", "DigitMode", "OCRVariant",
    "TemporalOCR", "TemporalObservation",
]
