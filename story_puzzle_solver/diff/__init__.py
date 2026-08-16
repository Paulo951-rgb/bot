"""Image difference engine + mask/region classification (rules 18, 19, 20, 21)."""
from .diff_engine import ImageDiffEngine, RegionDiff, MaskDetector
from .regions import RegionClassifier, RegionStatus

__all__ = [
    "ImageDiffEngine", "RegionDiff", "MaskDetector",
    "RegionClassifier", "RegionStatus",
]
