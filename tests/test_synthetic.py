"""Synthetic test scenarios (rules 54: TEST 1-10) + core pipeline tests.

Each test maps to a spec scenario:
  TEST 1: Card normal -> CARD DETECTED
  TEST 2: Card moved -> CARD DETECTED
  TEST 3: Card tilted -> CARD DETECTED
  TEST 4: Card slightly blurred -> CARD DETECTED or LOW CONFIDENCE
  TEST 5: Newly revealed zone -> NEWLY_REVEALED
  TEST 6: No new info -> NO UPDATE
  TEST 7: Video -> card detected during video
  TEST 8: Card visible only briefly -> frame captured
  TEST 9: Ambiguous OCR -> PARTIAL/UNKNOWN
  TEST 10: Two OCRs agree -> high confidence
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest
from pathlib import Path

from story_puzzle_solver.card import CardAligner, CardDetector, CardTemplate
from story_puzzle_solver.diff import ImageDiffEngine
from story_puzzle_solver.fusion import FusionInput, ResultFusionEngine
from story_puzzle_solver.ocr import OCREngine, TemporalOCR, TemporalObservation
from story_puzzle_solver.pipeline import PuzzlePipeline
from story_puzzle_solver.simulation.card_generator import render_card, place_card_in_frame
from story_puzzle_solver.state import PuzzleState
from story_puzzle_solver.video import VideoFrameEngine


# --------------------------------------------------------------------------- #
# TEST 1: Card normal -> CARD DETECTED
# --------------------------------------------------------------------------- #
def test_01_card_normal_detected(config, generator):
    """TEST 1: a normal card image should be detected."""
    det = CardDetector()
    values = generator._base_values()
    card, _, _ = render_card(values=values, revealed={"region_01": True}, rng=generator.rng)
    frame, _ = place_card_in_frame(card, offset=(0.5, 0.40), angle_deg=0.0, rng=generator.rng)
    d = det.detect(frame)
    assert d.detected, "card should be detected"
    assert d.confidence > 0.35


# --------------------------------------------------------------------------- #
# TEST 2: Card moved -> CARD DETECTED
# --------------------------------------------------------------------------- #
def test_02_card_moved_detected(config, generator):
    """TEST 2: a moved card should still be detected."""
    det = CardDetector()
    values = generator._base_values()
    card, _, _ = render_card(values=values, revealed={"region_01": True}, rng=generator.rng)
    for offset in [(0.35, 0.30), (0.65, 0.50), (0.45, 0.55)]:
        frame, _ = place_card_in_frame(card, offset=offset, angle_deg=0.0, rng=generator.rng)
        d = det.detect(frame)
        assert d.detected, f"card at offset {offset} should be detected"


# --------------------------------------------------------------------------- #
# TEST 3: Card tilted -> CARD DETECTED
# --------------------------------------------------------------------------- #
def test_03_card_tilted_detected(config, generator):
    """TEST 3: a tilted card should still be detected."""
    det = CardDetector()
    values = generator._base_values()
    card, _, _ = render_card(values=values, revealed={"region_01": True}, rng=generator.rng)
    for angle in [-8.0, -4.0, 3.0, 6.0]:
        frame, _ = place_card_in_frame(card, offset=(0.5, 0.42), angle_deg=angle, rng=generator.rng)
        d = det.detect(frame)
        assert d.detected, f"card at angle {angle} should be detected"


# --------------------------------------------------------------------------- #
# TEST 4: Card slightly blurred -> CARD DETECTED or LOW CONFIDENCE
# --------------------------------------------------------------------------- #
def test_04_card_blurred_detected_or_low(config, generator):
    """TEST 4: a blurred card should be detected (possibly low confidence)."""
    det = CardDetector()
    values = generator._base_values()
    card, _, _ = render_card(values=values, revealed={"region_01": True}, rng=generator.rng)
    frame, _ = place_card_in_frame(card, offset=(0.5, 0.40), angle_deg=0.0, rng=generator.rng)
    blurred = cv2.GaussianBlur(frame, (15, 15), 0)
    d = det.detect(blurred)
    # either detected or low confidence (rule 54 TEST 4)
    assert d.detected or d.confidence < 0.5, "blurred card: detected or low confidence"


# --------------------------------------------------------------------------- #
# TEST 5: Newly revealed zone -> NEWLY_REVEALED
# --------------------------------------------------------------------------- #
def test_05_newly_revealed(config, generator, scenario):
    """TEST 5: story_2 reveals region_04 -> NEWLY_REVEALED."""
    pipe = PuzzlePipeline(config)
    # process story_1 (region_01 revealed)
    s1 = scenario[1]
    r1 = pipe.process(s1, s1.media_path)
    # process story_2 (region_01 + region_04 revealed)
    s2 = scenario[2]
    r2 = pipe.process(s2, s2.media_path)
    newly = [u for u in r2.updates if u.status == "NEWLY_REVEALED" and u.value]
    assert any(u.region == "region_04" for u in newly), "region_04 should be NEWLY_REVEALED"


# --------------------------------------------------------------------------- #
# TEST 6: No new info -> NO UPDATE
# --------------------------------------------------------------------------- #
def test_06_no_new_info(config, generator, scenario):
    """TEST 6: reprocessing the same story should produce no new info."""
    pipe = Pipeline = PuzzlePipeline(config)
    s1 = scenario[1]
    pipe.process(s1, s1.media_path)
    # process a near-identical duplicate -> no new notifications
    r_dup = pipe.process(s1, s1.media_path)
    assert r_dup.notifications == 0, "duplicate story should not notify"


# --------------------------------------------------------------------------- #
# TEST 7: Video -> card detected during video
# --------------------------------------------------------------------------- #
def test_07_video_card_detected(config, generator, scenario):
    """TEST 7: video story should detect the card."""
    pipe = PuzzlePipeline(config)
    s3 = scenario[4]  # story_3 is the video
    r = pipe.process(s3, s3.media_path)
    assert r.media_kind == "VIDEO"
    assert r.card_detected, "card should be detected in video"


# --------------------------------------------------------------------------- #
# TEST 8: Card visible only briefly -> frame captured
# --------------------------------------------------------------------------- #
def test_08_card_briefly_visible(generator, config):
    """TEST 8: card visible only a few frames should still be captured."""
    ve = VideoFrameEngine(initial_sample_ms=100, focused_sample_ms=50)
    # generate a short video with card appearing briefly
    video_path = config.state_dir / "brief.mp4"
    generator.generate_brief_card_video(video_path, duration_s=3.0, card_appear_s=(1.0, 1.8))
    assert video_path.exists()
    found = []
    ve.best_candidates(video_path, k=3, on_frame=lambda c: found.append(c))
    assert len(found) > 0, "at least one frame with card should be captured"


# --------------------------------------------------------------------------- #
# TEST 9: Ambiguous OCR -> PARTIAL / UNKNOWN
# --------------------------------------------------------------------------- #
def test_09_ambiguous_ocr_partial():
    """TEST 9: ambiguous OCR should yield PARTIAL, not a guess."""
    temporal = TemporalOCR(min_confirmations=2)
    obs = [
        TemporalObservation(text="56?8", confidence=0.4, frame_index=0),
        TemporalObservation(text="56?8", confidence=0.4, frame_index=1),
    ]
    result = temporal.fuse(obs, expected_len=4)
    assert "?" in result.text, "ambiguous OCR must keep ? (rule 2)"
    assert result.status in ("PARTIAL", "UNKNOWN")


# --------------------------------------------------------------------------- #
# TEST 10: Two OCRs agree -> high confidence
# --------------------------------------------------------------------------- #
def test_10_two_ocr_agree_high_confidence():
    """TEST 10: repeated OCR value across frames -> boosted confidence."""
    temporal = TemporalOCR(min_confirmations=2)
    obs = [
        TemporalObservation(text="5678", confidence=0.8, frame_index=0, quality_score=0.8),
        TemporalObservation(text="5678", confidence=0.85, frame_index=1, quality_score=0.8),
        TemporalObservation(text="5678", confidence=0.82, frame_index=2, quality_score=0.8),
    ]
    result = temporal.fuse(obs, expected_len=4)
    assert result.text == "5678"
    assert result.status == "CONFIRMED"
    assert result.confidence > 0.8, "repeated values should boost confidence"


# --------------------------------------------------------------------------- #
# TEST: Full pipeline end-to-end produces correct number
# --------------------------------------------------------------------------- #
def test_full_pipeline_correct_number(config, scenario):
    """The full pipeline should extract all 16 digits correctly."""
    pipe = PuzzlePipeline(config)
    for s in scenario:
        pipe.process(s, s.media_path)
    fe = pipe.fast_entry()
    assert fe["number"]["display"] == "4532 8841 9023 5678", \
        f"expected full number, got {fe['number']['display']}"
    assert not fe["number"]["partial"]
    assert fe["cvv"]["display"] == "123"


# --------------------------------------------------------------------------- #
# TEST: Decoys are ignored (rule 74)
# --------------------------------------------------------------------------- #
def test_decoys_ignored(config, scenario):
    """Decoy stories should not trigger card detection or notifications."""
    pipe = PuzzlePipeline(config)
    decoy1 = scenario[0]
    r = pipe.process(decoy1, decoy1.media_path)
    assert not r.card_detected
    assert r.notifications == 0


# --------------------------------------------------------------------------- #
# TEST: Dedup (rule 8)
# --------------------------------------------------------------------------- #
def test_dedup(config, scenario):
    """Duplicate stories should be detected as cache hits."""
    from story_puzzle_solver.media import MediaDetector
    md = MediaDetector()
    s = scenario[0]
    info1 = md.analyze(s.media_path, "a")
    info2 = md.analyze(s.media_path, "b")
    md.remember(info1)
    dedup = md.check_dedup(info2)
    assert dedup.is_known, "identical media should dedup"


# --------------------------------------------------------------------------- #
# TEST: State persistence + restart (rule 51)
# --------------------------------------------------------------------------- #
def test_state_persistence(config, scenario):
    """PuzzleState should persist and reload after restart."""
    pipe = PuzzlePipeline(config)
    pipe.process(scenario[1], scenario[1].media_path)
    pipe.save_state()
    state_path = config.state_dir / "puzzle_state.json"
    assert state_path.exists()
    loaded = PuzzleState.load(state_path)
    assert loaded.regions["region_01"].value == "4532"


# --------------------------------------------------------------------------- #
# TEST: Notifications fire only on new info (rule 40)
# --------------------------------------------------------------------------- #
def test_notifications_only_on_new(config, scenario):
    """Notifications should only fire for genuinely new information."""
    pipe = PuzzlePipeline(config)
    s1 = scenario[1]
    r1 = pipe.process(s1, s1.media_path)
    n1 = r1.notifications
    # reprocess same -> no new notif
    r1b = pipe.process(s1, s1.media_path)
    assert r1b.notifications == 0
    # story_2 reveals region_04 -> 1 new notif
    s2 = scenario[2]
    r2 = pipe.process(s2, s2.media_path)
    assert r2.notifications >= 1


# --------------------------------------------------------------------------- #
# TEST: Reliability guard (rule 2, 71)
# --------------------------------------------------------------------------- #
def test_reliability_guard(config, scenario):
    """A reliable value must not be replaced by a less reliable one."""
    pipe = PuzzlePipeline(config)
    pipe.process(scenario[1], scenario[1].media_path)  # region_01 = 4532
    val_before = pipe.state.regions["region_01"].value
    # process story_2 (same value 4532, possibly different OCR quality)
    pipe.process(scenario[2], scenario[2].media_path)
    val_after = pipe.state.regions["region_01"].value
    assert val_after == val_before == "4532", "reliable value must persist"


# --------------------------------------------------------------------------- #
# TEST: Latency measured (rule 43, 44)
# --------------------------------------------------------------------------- #
def test_latency_measured(config, scenario):
    """media_to_result_ms should be measured and reasonable."""
    pipe = PuzzlePipeline(config)
    s4 = scenario[5]
    r = pipe.process(s4, s4.media_path)
    assert r.media_to_result_ms > 0
    assert r.media_to_result_ms < 60000, "latency should be under 60s"


# --------------------------------------------------------------------------- #
# TEST: Clipboard (rule 33, 34)
# --------------------------------------------------------------------------- #
def test_clipboard_display_vs_copy(config, scenario):
    """Display value has spaces, clipboard value is normalized (rule 34)."""
    pipe = PuzzlePipeline(config)
    for s in scenario:
        pipe.process(s, s.media_path)
    fe = pipe.fast_entry()
    assert " " in fe["number"]["display"], "display should be formatted with spaces"
    assert " " not in fe["number"]["clipboard"], "clipboard should be normalized (no spaces)"


# --------------------------------------------------------------------------- #
# TEST: Mask detection (rule 20)
# --------------------------------------------------------------------------- #
def test_mask_detection(config, generator, scenario):
    """Masked regions should be detected as MASKED."""
    pipe = PuzzlePipeline(config)
    s1 = scenario[1]  # only region_01 revealed, rest masked
    r = pipe.process(s1, s1.media_path)
    masked = [u for u in r.updates if u.status == "MASKED"]
    assert len(masked) >= 5, "masked regions should be detected"


# --------------------------------------------------------------------------- #
# TEST: Partial values shown with ? (rule 37)
# --------------------------------------------------------------------------- #
def test_partial_values_marked(config, scenario):
    """Partial card number should be marked partial and show ?."""
    pipe = PuzzlePipeline(config)
    pipe.process(scenario[1], scenario[1].media_path)  # only region_01
    fe = pipe.fast_entry()
    assert fe["number"]["partial"], "with only 1/4 groups, number should be partial"
    assert "?" in fe["number"]["display"] or "????" in fe["number"]["display"]
