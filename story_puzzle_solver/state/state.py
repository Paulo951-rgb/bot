"""PuzzleState — the evolving, persisted state of detected puzzle information.

Rules 29, 30, 31:
  - each region has value/confidence/status and full provenance
  - old values + their source are preserved (history)
  - novelty = compare previous vs current; only notify on NEW information
  - never replace a reliable value with a less reliable one without justification
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..card.template import RegionState


class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


@dataclass
class Provenance:
    value: str
    source_story: str
    source_frame: int = -1
    timestamp: str = ""
    ocr_provider: str = ""
    confidence: float = 0.0
    method: str = ""  # ocr | temporal | diff | vision | fusion


@dataclass
class RegionStateEntry:
    key: str
    value: Optional[str] = None
    confidence: float = 0.0
    status: str = "UNKNOWN"  # RegionState value
    confidence_level: str = "UNKNOWN"
    history: List[Provenance] = field(default_factory=list)
    last_updated: str = ""

    def is_newly_revealed(self) -> bool:
        return self.status == RegionState.NEWLY_REVEALED.value


class PuzzleState:
    def __init__(self):
        self.regions: Dict[str, RegionStateEntry] = {}

    def ensure(self, key: str) -> RegionStateEntry:
        if key not in self.regions:
            self.regions[key] = RegionStateEntry(key=key)
        return self.regions[key]

    def update(self, key: str, value: Optional[str], confidence: float,
               status: str, provenance: Provenance,
               force: bool = False) -> Tuple[bool, bool]:
        """Apply an update. Returns (changed, is_new_info).

        Rule: never replace a reliable value with a less reliable one without
        justification (force=True).
        """
        entry = self.ensure(key)
        prev_value = entry.value
        prev_conf = entry.confidence

        # reliability guard
        if not force and prev_value is not None and "?" not in prev_value:
            # existing is reliable (no '?')
            if value is None or "?" in value or confidence < prev_conf - 0.1:
                # keep previous, but record provenance in history
                entry.history.append(provenance)
                return False, False
            # a correction: previously partial now complete
            if "?" in prev_value and value and "?" not in value and confidence >= prev_conf:
                entry.value = value
                entry.confidence = confidence
                entry.status = status
                entry.confidence_level = confidence_level(confidence).value
                entry.history.append(provenance)
                entry.last_updated = provenance.timestamp
                return True, True  # correction counts as new info (rule 41)

        changed = (value != prev_value) or (status != entry.status)
        is_new_info = False
        if value is not None and value != prev_value:
            # genuinely new info: was None/partial/masked -> now has content
            if prev_value is None or prev_value != value:
                is_new_info = True
        if status == RegionState.NEWLY_REVEALED.value and entry.status != RegionState.NEWLY_REVEALED.value:
            is_new_info = True

        entry.value = value
        entry.confidence = max(entry.confidence, confidence) if value == prev_value else confidence
        entry.status = status
        entry.confidence_level = confidence_level(confidence).value
        entry.history.append(provenance)
        entry.last_updated = provenance.timestamp or datetime.now(timezone.utc).isoformat()
        return changed, is_new_info

    def snapshot(self) -> Dict[str, Any]:
        return {
            k: {
                "value": v.value,
                "confidence": round(v.confidence, 3),
                "status": v.status,
                "confidence_level": v.confidence_level,
                "last_updated": v.last_updated,
                "history_len": len(v.history),
            }
            for k, v in self.regions.items()
        }

    def display_value(self, key: str) -> str:
        e = self.regions.get(key)
        if not e or e.value is None:
            return ""
        return e.value

    def is_partial(self, key: str) -> bool:
        e = self.regions.get(key)
        return bool(e and e.value and "?" in e.value)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "regions": {
                k: {
                    "value": v.value, "confidence": v.confidence, "status": v.status,
                    "confidence_level": v.confidence_level, "last_updated": v.last_updated,
                    "history": [asdict(p) for p in v.history],
                } for k, v in self.regions.items()
            }
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "PuzzleState":
        ps = cls()
        if not path.exists():
            return ps
        data = json.loads(path.read_text(encoding="utf-8"))
        for k, d in data.get("regions", {}).items():
            entry = RegionStateEntry(
                key=k, value=d.get("value"), confidence=d.get("confidence", 0.0),
                status=d.get("status", "UNKNOWN"),
                confidence_level=d.get("confidence_level", "UNKNOWN"),
                last_updated=d.get("last_updated", ""),
            )
            for ph in d.get("history", []):
                entry.history.append(Provenance(**ph))
            ps.regions[k] = entry
        return ps

    @classmethod
    def load_initial(cls, path: Path) -> "PuzzleState":
        """Load user-provided initial known values (spec §3 BIS).

        Only values explicitly marked with a non-null ``value`` are loaded as
        KNOWN. Null/absent regions stay UNKNOWN. Never invents values.
        """
        ps = cls()
        if not path.exists():
            return ps
        data = json.loads(path.read_text(encoding="utf-8"))
        for k, d in data.get("regions", {}).items():
            value = d.get("value")
            status = d.get("status", "KNOWN" if value else "UNKNOWN")
            if value is not None:
                entry = RegionStateEntry(
                    key=k, value=value,
                    confidence=d.get("confidence", 1.0),
                    status=status if status != "UNKNOWN" else "KNOWN",
                    confidence_level="HIGH",
                    last_updated=d.get("last_updated", ""),
                )
                ps.regions[k] = entry
            else:
                ps.regions[k] = RegionStateEntry(key=k, status=status)
        return ps


def confidence_level(conf: float) -> ConfidenceLevel:
    if conf >= 0.85:
        return ConfidenceLevel.HIGH
    if conf >= 0.65:
        return ConfidenceLevel.MEDIUM
    if conf >= 0.4:
        return ConfidenceLevel.LOW
    return ConfidenceLevel.UNKNOWN
