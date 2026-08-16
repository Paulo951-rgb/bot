"""Media detection: type classification + dedup (rules 8, 9)."""
from .media_detector import MediaDetector, MediaInfo, MediaKind, DedupStore, DedupResult

__all__ = ["MediaDetector", "MediaInfo", "MediaKind", "DedupStore", "DedupResult"]
