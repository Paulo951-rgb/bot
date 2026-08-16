"""Result fusion engine (rules 27, 28, 71).

Fuses OCR, temporal OCR, ImageDiff, Vision, PuzzleState, frame quality into a
single PuzzleUpdate per region. Confidence is built from measurable evidence,
never invented.

Reliability hierarchy (rule 71): prefer stable multi-frame OCR > multi-confirm
OCR > OCR+diff > single sharp OCR > Vision > deduction (deduction forbidden).
"""
from .fusion import (
    ResultFusionEngine, FusionInput, PuzzleUpdate, Evidence,
)

__all__ = ["ResultFusionEngine", "FusionInput", "PuzzleUpdate", "Evidence"]
