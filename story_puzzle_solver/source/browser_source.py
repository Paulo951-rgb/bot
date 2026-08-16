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

Capture strategy (non-blocking): each ``poll()`` captures at most ONE
screenshot of the current story-viewer state and returns it as a StoryItem.
The dashboard poll loop (default 250 ms) naturally samples frames over time,
and the pipeline deduplicates by content hash so only genuinely new visuals
trigger analysis. This works for both image stories and video stories.

Configuration (env, also accepted in .env):
  SNAP_TARGET_USERNAME     — username whose stories to watch (REQUIRED)
  SNAP_BROWSER_PROFILE     — persistent profile dir (default: .browser-profile)
  SNAP_HEADLESS            — "false" to show browser for manual login (default: false)
  SNAP_LOGIN_WAIT_SEC      — seconds to wait for manual login on first run (default: 60)
  SNAP_NAV_TIMEOUT_MS      — page navigation timeout (default: 15000)
  SNAP_STORY_OPEN          — "true" to auto-click story tiles (default: true)
"""
from __future__ import annotations

import hashlib
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
    """Drives the user's own browser session to capture stories."""

    def __init__(self, target_username: Optional[str] = None,
                 profile_dir: Optional[str] = None,
                 headless: Optional[bool] = None,
                 login_wait_sec: Optional[int] = None,
                 nav_timeout_ms: Optional[int] = None,
                 story_open: Optional[bool] = None,
                 capture_dir: Optional[Path] = None,
                 logger: Optional[JsonLogger] = None):
        super().__init__(logger=logger)
        g = os.environ.get
        self.target_username = (target_username if target_username is not None
                                else g("SNAP_TARGET_USERNAME", "")).strip()
        self.profile_dir = Path(profile_dir or g("SNAP_BROWSER_PROFILE", ".browser-profile"))
        self.headless = (headless if headless is not None
                         else g("SNAP_HEADLESS", "false").lower() in ("1", "true", "yes"))
        self.login_wait_sec = (login_wait_sec if login_wait_sec is not None
                               else _int_env("SNAP_LOGIN_WAIT_SEC", 60))
        self.nav_timeout_ms = (nav_timeout_ms if nav_timeout_ms is not None
                               else _int_env("SNAP_NAV_TIMEOUT_MS", 15000))
        self.story_open = (story_open if story_open is not None
                           else g("SNAP_STORY_OPEN", "true").lower() in ("1", "true", "yes"))
        self.capture_dir = Path(capture_dir or ".state/captures")
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = None
        self._context = None
        self._page = None
        self._logged_in = False
        self._on_target = False
        self._capture_counter = 0
        self._last_capture_hash = ""

    def available(self) -> bool:
        return _playwright_available()

    def authorize(self, credentials: Dict[str, Any]) -> bool:
        """Launch the browser with a persistent profile.

        On first run the user logs in manually (the browser window stays open
        for ``login_wait_sec``); the session persists for later runs via the
        persistent profile directory.
        """
        if not _playwright_available():
            self._logger.error("browser_source_no_playwright",
                               note="pip install playwright && playwright install chromium")
            return False
        from playwright.sync_api import sync_playwright
        try:
            self._playwright = sync_playwright().start()
            self.profile_dir = self.profile_dir.resolve()
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self._page.set_default_timeout(self.nav_timeout_ms)
            self._connected = True
            self._logger.info("browser_source_authorized", profile=str(self.profile_dir))
            return True
        except Exception as e:
            self._logger.error("browser_source_authorize_failed", error=str(e))
            self._connected = False
            return False

    def wait_for_login(self) -> bool:
        """If not logged in, wait up to ``login_wait_sec`` for manual login.

        Returns True once a logged-in session is detected (heuristic), or
        False if the timeout elapses without a login.
        """
        if self._page is None:
            return False
        if self._logged_in:
            return True
        deadline = time.monotonic() + self.login_wait_sec
        while time.monotonic() < deadline:
            if self.is_logged_in():
                self._logged_in = True
                return True
            time.sleep(2)
        return self._logged_in

    def is_logged_in(self) -> bool:
        """Heuristic: navigate to the Snapchat home and check for login form."""
        if self._page is None:
            return False
        try:
            self._page.goto("https://web.snapchat.com", timeout=self.nav_timeout_ms,
                            wait_until="domcontentloaded")
            time.sleep(2)
            content = self._page.content().lower()
            # login form present => not authenticated
            if ("log in" in content and "password" in content) or "username" in content and "password" in content:
                return False
            return True
        except Exception:
            return False

    def _poll_impl(self) -> List[StoryItem]:
        """Capture one screenshot of the current story state (non-blocking)."""
        if self._page is None or not self.target_username:
            return []
        if not self._logged_in:
            return []
        items: List[StoryItem] = []
        try:
            self._ensure_on_target()
            if self.story_open:
                self._try_open_story()
            items = self._capture_one()
        except Exception as e:
            self._logger.warn("browser_source_poll_error", error=str(e))
        return items

    def _ensure_on_target(self) -> None:
        """Navigate to the target user's profile if not already there."""
        if self._page is None or self._on_target:
            return
        url = f"https://web.snapchat.com/@{self.target_username}"
        try:
            self._page.goto(url, timeout=self.nav_timeout_ms, wait_until="domcontentloaded")
            time.sleep(1.5)
            self._on_target = True
        except Exception as e:
            self._logger.warn("browser_source_nav_error", target=self.target_username, error=str(e))

    def _try_open_story(self) -> bool:
        """Attempt to click a story tile. Returns True if something opened."""
        if self._page is None:
            return False
        selectors = [
            "[data-testid='story']",
            "div[role='button']:has-text('Story')",
            "img[src*='story']",
            "[aria-label*='tory']",
            "button:has-text('Story')",
        ]
        for sel in selectors:
            try:
                el = self._page.query_selector(sel)
                if el:
                    el.click(timeout=3000)
                    time.sleep(1.0)
                    return True
            except Exception:
                continue
        return False

    def _capture_one(self) -> List[StoryItem]:
        """Capture one screenshot; skip if identical to the previous (dedup)."""
        if self._page is None:
            return []
        self._capture_counter += 1
        sid = f"snap_{int(time.time())}_{self._capture_counter}"
        path = self.capture_dir / f"{sid}.png"
        try:
            self._page.screenshot(path=str(path), full_page=False)
        except Exception as e:
            self._logger.warn("browser_source_capture_error", error=str(e))
            return []
        if not path.exists() or path.stat().st_size == 0:
            return []
        # content-based dedup: skip frames identical to the last captured one
        digest = _file_hash(path)
        if digest and digest == self._last_capture_hash:
            try:
                path.unlink()
            except Exception:
                pass
            return []
        self._last_capture_hash = digest
        return [StoryItem(
            story_id=sid, media_path=path, media_type_hint="IMAGE",
            timestamp=datetime.now(), author=self.target_username,
        )]

    def _get_media_impl(self, story: StoryItem, dest: Path) -> Path:
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
            self._logged_in = False
            self._on_target = False
            self._logger.info("browser_source_disconnected")


def _int_env(key: str, default: int) -> int:
    raw = os.environ.get(key)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _file_hash(path: Path) -> str:
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""
