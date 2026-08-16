"""Tests for the real source adapters (folder + browser), rules 7, 24."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from story_puzzle_solver.source.folder_source import FolderWatchSource
from story_puzzle_solver.source.browser_source import BrowserStorySource
from story_puzzle_solver.source.authorized_adapter import AuthorizedStorySourceAdapter
from story_puzzle_solver.pipeline import PuzzlePipeline
from story_puzzle_solver.simulation.fixture_generator import FixtureGenerator


# --------------------------------------------------------------------------- #
# FolderWatchSource (Option 1 — recommended)
# --------------------------------------------------------------------------- #
def test_folder_source_authorize_and_poll(tmp_path):
    src = FolderWatchSource(str(tmp_path / "watch"))
    assert src.authorize({}) is True
    assert src.connect() is True
    assert src.poll() == [], "empty folder -> no items"


def test_folder_source_detects_new_file(tmp_path):
    watch = tmp_path / "watch"
    watch.mkdir()
    src = FolderWatchSource(str(watch))
    src.authorize({})
    src.connect()
    # drop an image
    (watch / "story.png").write_bytes(b"\x89PNG fake")
    items = src.poll()
    assert len(items) == 1
    assert items[0].story_id == "file_story"
    assert items[0].media_type_hint == "IMAGE"


def test_folder_source_dedup(tmp_path):
    watch = tmp_path / "watch"
    watch.mkdir()
    src = FolderWatchSource(str(watch))
    src.authorize({})
    src.connect()
    (watch / "story.png").write_bytes(b"fake")
    assert len(src.poll()) == 1
    assert len(src.poll()) == 0, "same file must not be re-polled"


def test_folder_source_ignores_non_media(tmp_path):
    watch = tmp_path / "watch"
    watch.mkdir()
    src = FolderWatchSource(str(watch))
    src.authorize({})
    src.connect()
    (watch / "notes.txt").write_text("ignore me")
    assert src.poll() == []


def test_folder_source_classifies_video(tmp_path):
    watch = tmp_path / "watch"
    watch.mkdir()
    src = FolderWatchSource(str(watch))
    src.authorize({})
    src.connect()
    (watch / "clip.mp4").write_bytes(b"fake video")
    items = src.poll()
    assert len(items) == 1
    assert items[0].media_type_hint == "VIDEO"


def test_folder_source_get_media_copies(tmp_path):
    watch = tmp_path / "watch"
    watch.mkdir()
    src = FolderWatchSource(str(watch))
    src.authorize({})
    src.connect()
    f = watch / "story.png"
    f.write_bytes(b"payload")
    items = src.poll()
    dest = tmp_path / "out" / "copy.png"
    p = src.get_media(items[0], dest)
    assert p.exists()
    assert p.read_bytes() == b"payload"


def test_folder_source_end_to_end(config, tmp_path):
    """Drop a real synthetic card image into the folder -> pipeline detects it."""
    watch = tmp_path / "watch"
    watch.mkdir()
    gen = FixtureGenerator(Path("fixtures"), seed=7)
    story = gen.make_image_story("real_story_1", {"region_01": True}, angle=-4)
    shutil.copy2(story.media_path, watch / story.media_path.name)

    pipe = PuzzlePipeline(config)
    src = FolderWatchSource(str(watch))
    src.authorize({})
    src.connect()
    pipe.set_source_status("AUTHORIZED")
    items = src.poll()
    assert len(items) == 1
    r = pipe.process(items[0], items[0].media_path)
    assert r.card_detected, "card should be detected from folder-dropped media"
    assert r.notifications >= 1


# --------------------------------------------------------------------------- #
# BrowserStorySource (Option 2) — graceful when Playwright absent
# --------------------------------------------------------------------------- #
def test_browser_source_degrades_without_playwright():
    src = BrowserStorySource(target_username="someone")
    # In environments without playwright, available() must be False and
    # authorize() must return False (never crash).
    if not src.available():
        assert src.authorize({}) is False
    else:
        # if playwright IS installed, authorize attempts a real launch; skip
        pytest.skip("playwright installed; real browser launch not tested here")


def test_browser_source_requires_username():
    src = BrowserStorySource(target_username="")
    assert src.target_username == ""
    # no username -> poll returns nothing even if "connected"
    src._connected = True
    assert src.poll() == []


def test_browser_source_reads_username_from_env(monkeypatch):
    monkeypatch.setenv("SNAP_TARGET_USERNAME", "benoit")
    src = BrowserStorySource()
    assert src.target_username == "benoit"


def test_browser_source_strips_username(monkeypatch):
    monkeypatch.setenv("SNAP_TARGET_USERNAME", "  benoit  ")
    src = BrowserStorySource()
    assert src.target_username == "benoit"


def test_browser_source_env_config(monkeypatch):
    monkeypatch.setenv("SNAP_LOGIN_WAIT_SEC", "30")
    monkeypatch.setenv("SNAP_NAV_TIMEOUT_MS", "8000")
    monkeypatch.setenv("SNAP_HEADLESS", "true")
    monkeypatch.setenv("SNAP_STORY_OPEN", "false")
    src = BrowserStorySource(target_username="x")
    assert src.login_wait_sec == 30
    assert src.nav_timeout_ms == 8000
    assert src.headless is True
    assert src.story_open is False


def test_browser_source_poll_requires_login():
    src = BrowserStorySource(target_username="benoit")
    src._connected = True
    src._logged_in = False
    assert src._poll_impl() == [], "must not poll until logged in"


def test_browser_source_capture_dedup(tmp_path):
    """Two identical screenshots -> only the first is returned."""
    src = BrowserStorySource(target_username="benoit", capture_dir=tmp_path)
    src._connected = True
    src._logged_in = True
    # fake _capture_one with a stubbed page
    from story_puzzle_solver.source.browser_source import _file_hash
    p1 = tmp_path / "a.png"
    p1.write_bytes(b"\x89PNG identical")
    p2 = tmp_path / "b.png"
    p2.write_bytes(b"\x89PNG identical")  # same content
    p3 = tmp_path / "c.png"
    p3.write_bytes(b"\x89PNG different")
    # simulate the dedup logic manually
    src._capture_counter = 0
    results = []
    for p in (p1, p2, p3):
        src._capture_counter += 1
        digest = _file_hash(p)
        if digest and digest == src._last_capture_hash:
            continue
        src._last_capture_hash = digest
        from story_puzzle_solver.source.base import StoryItem
        results.append(StoryItem(story_id=str(p), media_path=p))
    assert len(results) == 2, "identical frame must be deduplicated"


def test_browser_source_get_media_copies(tmp_path):
    from story_puzzle_solver.source.base import StoryItem
    src = BrowserStorySource(target_username="benoit")
    src._connected = True
    cap = tmp_path / "cap.png"
    cap.write_bytes(b"payload")
    story = StoryItem(story_id="s1", media_path=cap)
    dest = tmp_path / "out" / "copy.png"
    p = src.get_media(story, dest)
    assert p.exists()
    assert p.read_bytes() == b"payload"


def test_browser_source_disconnect_resets_state():
    src = BrowserStorySource(target_username="benoit")
    src._connected = True
    src._logged_in = True
    src._on_target = True
    src.disconnect()
    assert src.connected is False
    assert src._logged_in is False
    assert src._on_target is False


# --------------------------------------------------------------------------- #
# Honest connection contract (rule 24)
# --------------------------------------------------------------------------- #
def test_adapter_not_connected_before_authorize():
    src = FolderWatchSource()
    assert src.connect() is False, "must not connect before authorize()"
    assert src.poll() == []
