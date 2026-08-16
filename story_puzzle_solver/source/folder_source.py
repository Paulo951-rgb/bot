"""Folder-watch authorized story source (rule 7, §24, Option 1 — RECOMMENDED).

The user saves story media (image/video) they are legitimately viewing into a
watched folder; this source detects new files and feeds them to the pipeline.
This is the most reliable day-J approach: the user supplies media they are
authorized to view, and the software only analyzes local files. No network
automation, no anti-bot, no auth circumvention (rule 7).

Configuration (env):
  WATCH_DIR — folder to watch for new media (default: fixtures/watch)
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..common.logger import JsonLogger
from .authorized_adapter import AuthorizedStorySourceAdapter
from .base import StoryItem

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


class FolderWatchSource(AuthorizedStorySourceAdapter):
    """Watches a folder for new media files the user drops in."""

    def __init__(self, watch_dir: Optional[str] = None,
                 capture_dir: Optional[Path] = None,
                 logger: Optional[JsonLogger] = None):
        super().__init__(logger=logger)
        self.watch_dir = Path(watch_dir or os.environ.get("WATCH_DIR", "fixtures/watch"))
        self.capture_dir = Path(capture_dir or ".state/captures")
        self._seen: set = set()

    def authorize(self, credentials: Dict[str, Any]) -> bool:
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        self._connected = True
        self._logger.info("folder_source_authorized", watch_dir=str(self.watch_dir))
        return True

    def _poll_impl(self) -> List[StoryItem]:
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        items: List[StoryItem] = []
        for p in sorted(self.watch_dir.iterdir()):
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext not in _IMAGE_EXTS and ext not in _VIDEO_EXTS:
                continue
            key = str(p.resolve()) + "|" + str(p.stat().st_mtime)
            if key in self._seen:
                continue
            self._seen.add(key)
            kind = "IMAGE" if ext in _IMAGE_EXTS else "VIDEO"
            items.append(StoryItem(
                story_id=f"file_{p.stem}",
                media_path=p, media_type_hint=kind,
                timestamp=datetime.now(),
            ))
        return items

    def _get_media_impl(self, story: StoryItem, dest: Path) -> Path:
        src = Path(story.media_path)
        if not src.exists():
            raise FileNotFoundError(f"media missing: {src}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest
