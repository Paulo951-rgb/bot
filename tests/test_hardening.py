"""Tests for the finalization/hardening work: honest Vision, notification
idempotency, clipboard logic, initial-state no-notify, source adapter contract,
and metrics (rules 10, 15, 24, 37)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from story_puzzle_solver.clipboard import ClipboardEngine, FieldValue
from story_puzzle_solver.notification import WindowsNotificationManager
from story_puzzle_solver.vision import VisionEngine, VisionResult
from story_puzzle_solver.source import AuthorizedStorySourceAdapter
from story_puzzle_solver.source.base import StoryItem
from story_puzzle_solver.config import Config
from story_puzzle_solver.pipeline import PuzzlePipeline
from story_puzzle_solver.simulation.fixture_generator import FixtureGenerator


# --------------------------------------------------------------------------- #
# Vision must be honestly UNAVAILABLE (rule 10)
# --------------------------------------------------------------------------- #
def test_vision_unavailable_by_default():
    v = VisionEngine(enabled=True)  # enabled but no backend
    assert v.available() is False
    assert v.status() == "UNAVAILABLE"
    res = v.recognize_region(None)
    assert res.available is False
    assert res.status == "UNAVAILABLE"
    # never a fake confidence=0 presented as a real analysis
    assert res.text == ""
    assert res.confidence == 0.0


def test_vision_unavailable_when_disabled():
    v = VisionEngine(enabled=False)
    assert v.available() is False


# --------------------------------------------------------------------------- #
# Notification idempotency (rule 15)
# --------------------------------------------------------------------------- #
def test_notification_idempotency(tmp_path):
    nm = WindowsNotificationManager(enabled=True, history_path=tmp_path / "n.json")
    r1 = nm.notify_new_info("region_02", "5678", 0.95, 120.0)
    r2 = nm.notify_new_info("region_02", "5678", 0.95, 120.0)  # same event
    assert r1.duplicate is False
    assert r2.duplicate is True, "duplicate event must be suppressed"
    # only one non-duplicate in history
    nondup = [r for r in nm.history() if not r.duplicate]
    assert len(nondup) == 1


def test_notification_different_value_not_duplicate(tmp_path):
    nm = WindowsNotificationManager(enabled=True, history_path=tmp_path / "n.json")
    nm.notify_new_info("region_02", "5678", 0.95, 120.0)
    r = nm.notify_new_info("region_02", "5679", 0.95, 120.0)
    assert r.duplicate is False


def test_notification_records_latency(tmp_path):
    nm = WindowsNotificationManager(enabled=True, history_path=tmp_path / "n.json")
    nm.notify_new_info("region_02", "5678", 0.95, 150.0)
    # the manager itself runs quickly; just ensure it doesn't crash and records
    assert len(nm.history()) == 1


# --------------------------------------------------------------------------- #
# Clipboard logic: display vs clipboard, partial not copied as complete (rule 37)
# --------------------------------------------------------------------------- #
def test_clipboard_display_vs_clipboard():
    ce = ClipboardEngine()
    fv = FieldValue(key="number", parts=["4532", "8841", "????", "????"], partial=True,
                    confidence=0.9, status="PARTIAL")
    disp = ce.build_display(fv)
    clip = ce.build_clipboard(fv)
    assert " " in disp, "display has separators"
    assert "????" in disp or "?" in disp, "partial shown with ?"
    assert "?" not in clip, "clipboard must not include unknown parts"
    assert clip == "45328841", "clipboard normalizes known parts only"


def test_clipboard_partial_not_copied_as_complete():
    ce = ClipboardEngine()
    fv = FieldValue(key="number", parts=["4532", "????"], partial=True, confidence=0.5)
    assert ce.copy_field(fv) is False, "partial value must not copy as complete by default"


def test_clipboard_complete_copies():
    ce = ClipboardEngine()
    fv = FieldValue(key="cvv", parts=["123"], partial=False, confidence=0.95)
    assert ce.copy_field(fv) is True


# --------------------------------------------------------------------------- #
# Initial-state known values must NOT notify (rule 12)
# --------------------------------------------------------------------------- #
def test_initial_known_value_not_notified(config, scenario):
    """A value present in puzzle_initial_state must not trigger a notification."""
    cfg = config
    # story_2 reveals region_01 and region_04; seed BOTH as known
    initial = {"regions": {
        "region_01": {"value": "4532", "status": "KNOWN"},
        "region_04": {"value": "5678", "status": "KNOWN"},
    }}
    cfg.project_root = cfg.state_dir.parent
    idir = cfg.project_root / "config"
    idir.mkdir(exist_ok=True)
    (idir / "puzzle_initial_state.json").write_text(json.dumps(initial))
    pipe = PuzzlePipeline(cfg)
    assert pipe.state.regions["region_01"].value == "4532"
    assert pipe.state.regions["region_04"].value == "5678"
    s2 = scenario[2]  # reveals region_01 + region_04 (both already known)
    r = pipe.process(s2, s2.media_path)
    assert r.notifications == 0, "already-known values must not notify"


# --------------------------------------------------------------------------- #
# AuthorizedStorySource adapter contract: honest connection (rule 24)
# --------------------------------------------------------------------------- #
class _FakeAuthorized(AuthorizedStorySourceAdapter):
    def __init__(self):
        super().__init__()
        self._authorized = False

    def authorize(self, credentials):
        self._authorized = True
        self._connected = True
        return True

    def _poll_impl(self):
        return []

    def _get_media_impl(self, story, dest):
        return dest


def test_authorized_adapter_not_connected_without_authorize():
    src = _FakeAuthorized()
    assert src.connect() is False, "must not connect without authorize()"
    assert src.poll() == []


def test_authorized_adapter_connects_after_authorize():
    src = _FakeAuthorized()
    assert src.authorize({"token": "x"}) is True
    assert src.connect() is True
    assert src.poll() == []


# --------------------------------------------------------------------------- #
# Pipeline exposes honest source + vision status
# --------------------------------------------------------------------------- #
def test_pipeline_source_status_default_disconnected(config):
    pipe = PuzzlePipeline(config)
    assert pipe.source_status == "DISCONNECTED"
    assert pipe.vision_status() == "UNAVAILABLE"


# --------------------------------------------------------------------------- #
# Metrics include new stages (rule 43)
# --------------------------------------------------------------------------- #
def test_metrics_new_stages_present(config, scenario):
    from story_puzzle_solver.common.metrics import STAGES
    assert "state_latency_ms" in STAGES
    assert "download_latency_ms" in STAGES
    assert "media_to_result_ms" in STAGES
