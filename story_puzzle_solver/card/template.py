"""CardTemplate — relative structure of the card (rule 15).

All region coordinates are NORMALIZED (0..1) relative to the aligned card
(rule 4: never hardcode absolute pixels). The template evolves: when a new
high-quality card is detected, its structure can refine the template's known/
masked/revealed zones.

This module defines the canonical region layout used across the system. It is
intentionally decoupled from the simulation card generator so the detector
relies on *learned relative structure*, not simulator internals.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Card aspect ratio (ISO/IEC 7810 ID-1): width/height ~ 1.586
CARD_ASPECT = 1.586


class RegionState(str, Enum):
    UNKNOWN = "UNKNOWN"
    MASKED = "MASKED"
    VISIBLE = "VISIBLE"
    NEWLY_REVEALED = "NEWLY_REVEALED"
    MODIFIED = "MODIFIED"
    PARTIAL = "PARTIAL"


@dataclass
class CardRegionSpec:
    key: str
    kind: str  # "digits" | "text"
    rect: Tuple[float, float, float, float]  # (x, y, w, h) normalized
    length: int = 0  # expected char count for digits
    label: str = ""  # semantic name: CARDHOLDER_NAME / CARD_NUMBER / EXPIRATION_DATE / CVC


# Canonical puzzle regions (relative coordinates on the NORMALIZED card).
# Vertical layout per spec §3 BIS:
#   1. CARDHOLDER_NAME  — top of info zone
#   2. CARD_NUMBER      — below the name
#   3. EXPIRATION + CVC — bottom, side by side (NOT to the right of the number)
REGION_DEFS: Dict[str, CardRegionSpec] = {
    "region_name": CardRegionSpec("region_name", "text", (0.06, 0.30, 0.55, 0.08), 0, "CARDHOLDER_NAME"),
    "region_01": CardRegionSpec("region_01", "digits", (0.06, 0.46, 0.20, 0.10), 4, "CARD_NUMBER"),
    "region_02": CardRegionSpec("region_02", "digits", (0.27, 0.46, 0.20, 0.10), 4, "CARD_NUMBER"),
    "region_03": CardRegionSpec("region_03", "digits", (0.48, 0.46, 0.20, 0.10), 4, "CARD_NUMBER"),
    "region_04": CardRegionSpec("region_04", "digits", (0.69, 0.46, 0.20, 0.10), 4, "CARD_NUMBER"),
    "region_exp": CardRegionSpec("region_exp", "text", (0.06, 0.70, 0.16, 0.09), 0, "EXPIRATION_DATE"),
    "region_cvv": CardRegionSpec("region_cvv", "digits", (0.26, 0.70, 0.12, 0.09), 3, "CVC"),
}

# Semantic field -> region keys mapping (spec §3 BIS).
FIELD_TO_REGIONS: Dict[str, List[str]] = {
    "CARDHOLDER_NAME": ["region_name"],
    "CARD_NUMBER": ["region_01", "region_02", "region_03", "region_04"],
    "EXPIRATION_DATE": ["region_exp"],
    "CVC": ["region_cvv"],
}

# Non-puzzle structural zones (excluded from analysis / used for similarity).
ZONE_WORLD_ELITE = (0.06, 0.08, 0.40, 0.08)
ZONE_CONTACTLESS = (0.82, 0.08, 0.12, 0.10)

# Snapcode exclusion: lives BELOW the card bottom edge. In normalized *frame*
# terms it sits just under y=1.0 of the card box. The detector/aligner simply
# crops to the card quadrilateral, which excludes anything below it.
SNAPCODE_EXCLUDE_REL = (0.30, 1.02, 0.40, 0.18)


@dataclass
class CardTemplate:
    aspect: float = CARD_ASPECT
    regions: Dict[str, CardRegionSpec] = field(default_factory=lambda: dict(REGION_DEFS))
    # learned state per region
    known_states: Dict[str, RegionState] = field(default_factory=dict)
    # reference normalized card signatures (perceptual hashes) for similarity
    reference_hashes: List[str] = field(default_factory=list)
    version: int = 1

    def region_keys(self) -> List[str]:
        return list(self.regions.keys())

    def region_rect(self, key: str) -> Tuple[float, float, float, float]:
        return self.regions[key].rect

    def learn_from(self, normalized_card_hash: str, states: Dict[str, RegionState]) -> None:
        """Refine the template when a high-quality card is detected (rule 15)."""
        if normalized_card_hash and normalized_card_hash not in self.reference_hashes:
            self.reference_hashes.append(normalized_card_hash)
            if len(self.reference_hashes) > 8:
                self.reference_hashes = self.reference_hashes[-8:]
        for k, s in states.items():
            if s == RegionState.VISIBLE and self.known_states.get(k) != RegionState.VISIBLE:
                self.known_states[k] = RegionState.VISIBLE
        self.version += 1

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "aspect": self.aspect,
            "regions": {k: asdict(v) for k, v in self.regions.items()},
            "known_states": {k: s.value for k, s in self.known_states.items()},
            "reference_hashes": self.reference_hashes,
            "version": self.version,
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "CardTemplate":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        t = cls(aspect=data.get("aspect", CARD_ASPECT))
        regs = {}
        for k, d in data.get("regions", {}).items():
            regs[k] = CardRegionSpec(d["key"], d["kind"], tuple(d["rect"]), d.get("length", 0), d.get("label", ""))
        t.regions = regs or dict(REGION_DEFS)
        t.known_states = {k: RegionState(s) for k, s in data.get("known_states", {}).items()}
        t.reference_hashes = list(data.get("reference_hashes", []))
        t.version = data.get("version", 1)
        return t
