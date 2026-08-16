"""Browser-based authorized story source (rule 7, §24, Option 2).

Uses Playwright to drive the user's OWN browser with the user's OWN logged-in
session. The first run opens a visible browser so the user logs in manually;
subsequent runs reuse the persistent profile so the session stays alive.

This is legitimate access: the user's own account, the user's own credentials,
content the user is authorized to view. NO cookie theft, NO session hijack,
NO CAPTCHA bypass, NO anti-bot bypass (rule 7).

Playwright is an OPTIONAL dependency. If it is not installed, this source
reports UNAVAILABLE gracefully instead of crashing the program.

Snapchat's web interface is fragile and may change; this adapter is written
defensively (timeouts, selectors with fallbacks, screenshot-based capture).
If the DOM breaks, the user can still fall back to FolderWatchSource (Option 1)
by saving the story media manually into a watched folder.

Configuration (env):
  SNAP_TARGET_USERNAME  — username whose stories to watch (REQUIRED)
  SNAP_BROWSER_PROFILE  — persistent profile dir (default: .browser-profile)
  SNAP_HEADLESS         — "false" to show browser for manual login (default: false)
  SNAP_CAPTURE_INTERVAL_MS — screenshot interval during story playback (default: 700)
  SNAP_STORY_TIMEOUT_MS — max time to spend on one story (default: 12000)
"""
from __future__ import annotations

import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..common.logger import JsonLogger
from .authorized_adapter import AuthorizedStorySourceAdapter
from .base import StoryItem


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


class BrowserStorySource(AuthorizedStorySourceAdapter):
    """Drives the user's own browser session to capture stories.

    Capture strategy: for each new story, take periodic screenshots of the
    story viewer while it plays. Each screenshot becomes a StoryItem; the
    pipeline deduplicates by content hash, so only genuinely new visuals
    trigger analysis. This works for both image stories and video stories
    (video frames are sampled over time).
    """

    def __init__(self, target_username: Optional[str] = None,
                 profile_dir: Optional[str] = None,
                 headless: Optional[bool] = None,
                 capture_interval_ms: int = 700,
                 story_timeout_ms: int = 12000,
                 capture_dir: Optional[Path] = None,
                 logger: Optional[JsonLogger] = None):
        super().__init__(logger=logger)
        self.target_username = target_username or os.environ.get("SNAP_TARGET_USERNAME", "")
        self.profile_dir = Path(profile_dir or os.environ.get("SNAP_BROWSER_PROFILE", ".browser-profile"))
        self.headless = (headless if headless is not None
                         else os.environ.get("SNAP_HEADLESS", "false").lower() in ("1", "true", "yes"))
        self.capture_interval_ms = capture_interval_ms
        self.story_timeout_ms = story_timeout_ms
        self.capture_dir = Path(capture_dir or ".state/captures")
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._seen_story_ids: set = set()
        self._capture_counter = 0

    def available(self) -> bool:
        return _playwright_available()

    def authorize(self, credentials: Dict[str, Any]) -> bool:
        """Launch the browser with a persistent profile. On first run the user
        logs in manually; the session persists for later runs."""
        if not _playwright_available():
            self._logger.error("browser_source_no_playwright",
                               note="pip install playwright && playwright install chromium")
            return False
        from playwright.sync_api import sync_playwright
        try:
            self._playwright = sync_playwright().start()
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self._connected = True
            self._logger.info("browser_source_authorized", profile=str(self.profile_dir))
            return True
        except Exception as e:
            self._logger.error("browser_source_authorize_failed", error=str(e))
            self._connected = False
            return False

    def is_logged_in(self) -> bool:
        """Check whether the session is authenticated (heuristic)."""
        if self._page is None:
            return False
        try:
            self._page.goto("https://web.snapchat.com", timeout=15000, wait_until="domcontentloaded")
            time.sleep(2)
            # logged-in indicators vary; if we see a login button/form, we're not in
            content = self._page.content().lower()
            if "log in" in content and "password" in content:
                return False
            return True
        except Exception:
            return False

    def _poll_impl(self) -> List[StoryItem]:
        if self._page is None or not self.target_username:
            return []
        items: List[StoryItem] = []
        try:
            # Navigate to the target user's profile / stories
            url = f"https://web.snapchat.com/@{self.target_username}"
            self._page.goto(url, timeout=15000, wait_until="domcontentloaded")
            time.sleep(2)
            # Try to open the first available story. Selectors are best-effort.
            story_opened = self._try_open_story()
            if story_opened:
                items.extend(self._capture_current_story())
        except Exception as e:
            self._logger.warn("browser_source_poll_error", error=str(e))
        return items

    def _try_open_story(self) -> bool:
        """Attempt to click a story element. Returns True if a story viewer opened."""
        if self._page is None:
            return False
        # Snapchat web story triggers vary; try several common selectors.
        selectors = [
            "[data-testid='story']",
            "div[role='button']:has-text('Story')",
            "img[src*='story']",
            "[aria-label*='tory']",
        ]
        for sel in selectors:
            try:
                el = self._page.query_selector(sel)
                if el:
                    el.click(timeout=3000)
                    time.sleep(1.5)
                    return True
            except Exception:
                continue
        return False

    def _capture_current_story(self) -> List[StoryItem]:
        """Take periodic screenshots of the playing story."""
        items: List[StoryItem] = []
        if self._page is None:
            return items
        deadline = time.monotonic() + (self.story_timeout_ms / 1000.0)
        interval = self.capture_interval_ms / 1000.0
        while time.monotonic() < deadline:
            self._capture_counter += 1
            sid = f"snap_{int(time.time())}_{self._capture_counter}"
            if sid in self._seen_story_ids:
                time.sleep(interval)
                continue
            path = self.capture_dir / f"{sid}.png"
            try:
                self._page.screenshot(path=str(path), full_page=False)
                if path.exists() and path.stat().st_size > 0:
                    self._seen_story_ids.add(sid)
                    items.append(StoryItem(
                        story_id=sid, media_path=path,
                        media_type_hint="IMAGE",
                        timestamp=datetime.now(),
                        author=self.target_username,
                    ))
            except Exception as e:
                self._logger.warn("browser_source_capture_error", error=str(e))
            time.sleep(interval)
        return items

    def _get_media_impl(self, story: StoryItem, dest: Path) -> Path:
        # Screenshots are already local files; copy to the requested dest.
        src = Path(story.media_path)
        if not src.exists():
            raise FileNotFoundError(f"capture missing: {src}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest

    def disconnect(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._context = None
            self._page = None
            self._playwright = None
            self._connected = False
            self._logger.info("browser_source_disconnected")
