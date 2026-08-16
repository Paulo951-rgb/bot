"""Temporal OCR — fuse observations across frames/stories (rule 25).

Example:
  Frame 37 -> 56?8
  Frame 38 -> 5678
  Frame 39 -> 5678
  Frame 40 -> 5678
=> 5678 with boosted confidence (repeated on multiple clear frames).

A single ambiguous observation is kept as PARTIAL (rule 2: never invent).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class TemporalObservation:
    text: str
    confidence: float
    frame_index: int = -1
    story_id: str = ""
    quality_score: float = 0.0


@dataclass
class TemporalResult:
    text: str
    confidence: float
    status: str  # CONFIRMED | PARTIAL | UNKNOWN
    confirmations: int
    per_position: Dict[int, Dict[str, int]] = field(default_factory=dict)


class TemporalOCR:
    def __init__(self, min_confirmations: int = 2, min_confidence: float = 0.6):
        self.min_confirmations = min_confirmations
        self.min_confidence = min_confidence

    def fuse(self, observations: List[TemporalObservation], expected_len: Optional[int] = None) -> TemporalResult:
        if not observations:
            return TemporalResult(text="", confidence=0.0, status="UNKNOWN", confirmations=0)

        # per-position voting, tolerating '?' for ambiguous chars (rule 2)
        max_len = max((len(o.text) for o in observations), default=0)
        if expected_len:
            max_len = max(max_len, expected_len)
        per_pos: Dict[int, Counter] = defaultdict(Counter)
        for o in observations:
            text = o.text.ljust(max_len, "?")
            for i in range(max_len):
                ch = text[i]
                if ch == "?" or ch == "":
                    continue
                weight = o.confidence * (0.5 + 0.5 * o.quality_score)
                per_pos[i][ch] += weight

        out_chars: List[str] = []
        pos_detail: Dict[int, Dict[str, int]] = {}
        total_conf = 0.0
        confirmed_positions = 0
        for i in range(max_len):
            counter = per_pos[i]
            if not counter:
                out_chars.append("?")
                pos_detail[i] = {"?": 0}
                continue
            best_ch, best_w = counter.most_common(1)[0]
            total_weight = sum(counter.values())
            agreement = best_w / total_weight if total_weight else 0.0
            n_obs = len([o for o in observations if len(o.text) > i and o.text[i] == best_ch])
            pos_detail[i] = {ch: round(w, 2) for ch, w in counter.items()}
            if n_obs >= self.min_confirmations and agreement >= 0.6:
                out_chars.append(best_ch)
                total_conf += agreement
                confirmed_positions += 1
            else:
                # ambiguous => keep ? (rule 2)
                out_chars.append("?")

        text = "".join(out_chars)
        avg_conf = total_conf / max_len if max_len else 0.0
        if "?" in text:
            status = "PARTIAL"
            confidence = avg_conf * 0.8
        elif confirmed_positions == max_len and len(observations) >= self.min_confirmations:
            status = "CONFIRMED"
            confidence = min(1.0, avg_conf * (1.0 + 0.1 * min(len(observations), 5)))
        else:
            status = "PARTIAL"
            confidence = avg_conf * 0.7
        return TemporalResult(
            text=text, confidence=confidence, status=status,
            confirmations=len(observations), per_position=pos_detail,
        )
