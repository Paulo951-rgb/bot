"""ResultFusionEngine (rules 27, 28, 71)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..card.template import RegionState
from ..common.logger import JsonLogger
from ..common.timing import Timer
from ..state.state import PuzzleState, Provenance, confidence_level, ConfidenceLevel


@dataclass
class Evidence:
    ocr: bool = False
    temporal: bool = False
    diff: bool = False
    vision: bool = False


@dataclass
class FusionInput:
    region_key: str
    ocr_text: str = ""
    ocr_confidence: float = 0.0
    ocr_provider: str = ""
    temporal_text: str = ""
    temporal_confidence: float = 0.0
    temporal_status: str = "UNKNOWN"
    temporal_confirmations: int = 0
    diff_score: float = 0.0
    is_masked: bool = False
    prev_was_masked: bool = False
    vision_text: str = ""
    vision_confidence: float = 0.0
    frame_quality: float = 0.0
    source_story: str = ""
    source_frame: int = -1


@dataclass
class PuzzleUpdate:
    region: str
    value: Optional[str]
    confidence: float
    status: str
    confidence_level: str
    evidence: Evidence
    is_new_info: bool
    is_correction: bool = False
    provenance: Optional[Provenance] = None


class ResultFusionEngine:
    def __init__(self, notify_min_confidence: float = 0.75,
                 newly_threshold: float = 0.15, logger: Optional[JsonLogger] = None):
        self.notify_min_confidence = notify_min_confidence
        self.newly_threshold = newly_threshold
        self._logger = logger or JsonLogger("fusion")

    def fuse(self, inp: FusionInput, state: PuzzleState) -> PuzzleUpdate:
        with Timer() as t:
            upd = self._fuse_inner(inp, state)
        self._logger.info("fusion", region=inp.region_key,
                         confidence=round(upd.confidence, 3),
                         status=upd.status, latency_ms=round(t.elapsed_ms, 2))
        return upd

    def _fuse_inner(self, inp: FusionInput, state: PuzzleState) -> PuzzleUpdate:
        key = inp.region_key
        entry = state.ensure(key)
        prev_value = entry.value
        prev_status = entry.status
        evidence = Evidence()

        # MASKED handling
        if inp.is_masked:
            status = RegionState.MASKED.value
            return self._make_update(key, None, 0.9, status, evidence,
                                     is_new=False, inp=inp, state=state)

        # choose best text source by reliability hierarchy (rule 71)
        candidates = []
        if inp.temporal_text and inp.temporal_status in ("CONFIRMED", "PARTIAL"):
            evidence.temporal = True
            candidates.append((inp.temporal_text, inp.temporal_confidence, "temporal",
                              inp.temporal_confirmations))
        if inp.ocr_text:
            evidence.ocr = True
            candidates.append((inp.ocr_text, inp.ocr_confidence, inp.ocr_provider or "ocr", 1))
        if inp.vision_text and inp.vision_confidence > 0:
            evidence.vision = True
            candidates.append((inp.vision_text, inp.vision_confidence, "vision", 1))

        # if diff shows change, mark evidence
        if inp.diff_score > self.newly_threshold:
            evidence.diff = True

        if not candidates:
            # no OCR signal at all
            if inp.diff_score > self.newly_threshold and prev_status == RegionState.MASKED.value:
                status = RegionState.NEWLY_REVEALED.value
            else:
                status = RegionState.UNKNOWN.value
            return self._make_update(key, None, 0.2, status, evidence,
                                     is_new=False, inp=inp, state=state)

        # pick best candidate, but boost when multiple agree (rule 28, 71)
        candidates.sort(key=lambda c: (c[1], c[3]), reverse=True)
        best_text, best_conf, best_method, n_conf = candidates[0]

        # cross-source agreement boost
        texts = [c[0] for c in candidates]
        if len(texts) >= 2 and len({t for t in texts}) == 1:
            best_conf = min(1.0, best_conf + 0.1)
            if n_conf < 2:
                n_conf = 2
        # temporal boost
        if evidence.temporal and inp.temporal_status == "CONFIRMED":
            best_conf = min(1.0, best_conf + 0.05)

        # quality penalty
        best_conf *= (0.7 + 0.3 * max(0.0, min(1.0, inp.frame_quality)))

        # determine status
        if prev_status == RegionState.MASKED.value and (evidence.diff or inp.diff_score > self.newly_threshold):
            status = RegionState.NEWLY_REVEALED.value
        elif prev_value and prev_value != best_text and "?" not in (prev_value or ""):
            status = RegionState.MODIFIED.value
        elif "?" in best_text:
            status = RegionState.PARTIAL.value
        else:
            status = RegionState.VISIBLE.value

        # is new info? (rule 31)
        is_new = False
        is_correction = False
        if prev_value is None:
            is_new = bool(best_text)
        elif prev_value != best_text:
            if "?" in prev_value and "?" not in best_text:
                is_correction = True
                is_new = True
            elif best_text:
                is_new = True

        # rule 2: never invent. If text empty or fully '?', value is None.
        value = best_text if best_text and best_text != "?" * len(best_text) else None
        if value is None and best_text and "?" in best_text:
            # partial: keep the partial string with '?' (rule 37)
            value = best_text
            status = RegionState.PARTIAL.value
            best_conf *= 0.7

        prov = Provenance(
            value=best_text, source_story=inp.source_story, source_frame=inp.source_frame,
            timestamp=datetime.now(timezone.utc).isoformat(),
            ocr_provider=best_method, confidence=best_conf, method=best_method,
        )
        return PuzzleUpdate(
            region=key, value=value, confidence=best_conf, status=status,
            confidence_level=confidence_level(best_conf).value, evidence=evidence,
            is_new_info=is_new, is_correction=is_correction, provenance=prov,
        )

    def _make_update(self, key, value, conf, status, evidence, is_new, inp, state) -> PuzzleUpdate:
        prov = Provenance(
            value=value or "", source_story=inp.source_story, source_frame=inp.source_frame,
            timestamp=datetime.now(timezone.utc).isoformat(),
            ocr_provider="", confidence=conf, method="fusion",
        )
        return PuzzleUpdate(
            region=key, value=value, confidence=conf, status=status,
            confidence_level=confidence_level(conf).value, evidence=evidence,
            is_new_info=is_new, provenance=prov,
        )

    def should_notify(self, upd: PuzzleUpdate) -> bool:
        """Notify only on new, sufficiently reliable info (rule 40)."""
        if not upd.is_new_info:
            return False
        if upd.status in (RegionState.MASKED.value, RegionState.UNKNOWN.value):
            return False
        if upd.value is None:
            return False
        # partial: notify only if it's a correction to a better value
        if "?" in (upd.value or "") and not upd.is_correction:
            return False
        return upd.confidence >= self.notify_min_confidence
