"""Video frame engine: 3-level scan/focus/extraction + early exit (rules 11-13)."""
from .frame_engine import (
    VideoFrameEngine,
    FrameCandidate,
    FrameQuality,
    ScanLevel,
)

__all__ = ["VideoFrameEngine", "FrameCandidate", "FrameQuality", "ScanLevel"]
