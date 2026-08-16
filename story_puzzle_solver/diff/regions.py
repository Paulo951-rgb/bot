"""Region status classification (rule 19).

Per-region states: UNKNOWN, MASKED, VISIBLE, NEWLY_REVEALED, MODIFIED, PARTIAL.
Wraps the RegionState enum from the card template with classification logic
that combines mask detection + diff results.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..card.template import RegionState


@dataclass
class RegionStatus:
    key: str
    state: RegionState
    confidence: float
    diff_score: float = 0.0
    is_masked: bool = False


class RegionClassifier:
    """Classify a region's state given mask + diff signals."""

    def __init__(self, newly_revealed_threshold: float = 0.15,
                 modified_threshold: float = 0.08):
        self.newly_threshold = newly_revealed_threshold
        self.modified_threshold = modified_threshold

    def classify(self, key: str, is_masked: bool, diff_score: float,
                 prev_was_masked: bool, current_text_known: bool) -> RegionStatus:
        if is_masked:
            # currently masked
            state = RegionState.MASKED
            conf = 0.9
        elif prev_was_masked and diff_score > self.newly_threshold:
            # was masked, now differs strongly => newly revealed
            state = RegionState.NEWLY_REVEALED
            conf = min(1.0, 0.5 + diff_score)
        elif diff_score > self.modified_threshold:
            state = RegionState.MODIFIED
            conf = min(0.9, 0.4 + diff_score)
        elif current_text_known:
            state = RegionState.VISIBLE
            conf = 0.85
        else:
            state = RegionState.UNKNOWN
            conf = 0.2
        return RegionStatus(key=key, state=state, confidence=conf,
                            diff_score=diff_score, is_masked=is_masked)
