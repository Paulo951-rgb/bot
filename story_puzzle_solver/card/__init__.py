"""Card detection + template + alignment (rules 14, 15, 16)."""
from .template import CardTemplate, REGION_DEFS, CardRegionSpec, RegionState, FIELD_TO_REGIONS
from .detector import CardDetector, CardDetection, CARD_ASPECT
from .alignment import CardAligner, NormalizedCard

__all__ = [
    "CardTemplate", "REGION_DEFS", "CardRegionSpec", "RegionState", "FIELD_TO_REGIONS",
    "CardDetector", "CardDetection", "CARD_ASPECT",
    "CardAligner", "NormalizedCard",
]
