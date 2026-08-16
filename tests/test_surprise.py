"""Surprise random scenario + recovery + benchmark tests (rules 50, 56, 57, 80, 82)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from story_puzzle_solver.pipeline import PuzzlePipeline
from story_puzzle_solver.simulation.fixture_generator import FixtureGenerator
from story_puzzle_solver.source import SimulationStorySource


@pytest.mark.parametrize("seed", [1, 42, 123])
def test_surprise_scenario(config, seed):
    """Rule 82: randomized surprise scenario must stay functional."""
    gen = FixtureGenerator(Path("fixtures"), seed=seed)
    scenario = gen.surprise_scenario(seed=seed)
    pipe = PuzzlePipeline(config)
    ok = True
    for s in scenario:
        try:
            r = pipe.process(s, s.media_path)
            # should never crash; card detection optional but must be a boolean
            assert isinstance(r.card_detected, bool)
        except Exception:
            ok = False
            break
    assert ok, "surprise scenario must not crash"


def test_recovery_after_error(config, scenario):
    """Rule 50: pipeline should survive a bad story and continue."""
    pipe = PuzzlePipeline(config)
    # process a good story
    pipe.process(scenario[1], scenario[1].media_path)
    # corrupt story (non-existent path) should not crash the pipeline
    bad = scenario[0]
    bad_path = Path("/nonexistent/corrupt.png")
    try:
        pipe.process(bad, bad_path)
    except Exception as e:
        pytest.fail(f"pipeline should handle corrupt media gracefully: {e}")
    # pipeline should still work after the bad story
    r = pipe.process(scenario[2], scenario[2].media_path)
    assert r.card_detected


def test_restart_recovers_state(config, scenario):
    """Rule 51: after restart, state should be recovered."""
    pipe1 = PuzzlePipeline(config)
    pipe1.process(scenario[1], scenario[1].media_path)
    pipe1.process(scenario[2], scenario[2].media_path)
    pipe1.save_state()
    # simulate restart: new pipeline loads state
    pipe2 = PuzzlePipeline(config)
    fe = pipe2.fast_entry()
    # state should contain region_01 and region_04 from the saved state
    assert pipe2.state.regions["region_01"].value == "4532"
    assert pipe2.state.regions["region_04"].value == "5678"


def test_benchmark_latency(config, scenario):
    """Rule 43/56: benchmark the full chain and report MEDIA -> RESULT."""
    pipe = PuzzlePipeline(config)
    pipe.prewarm()
    latencies = []
    for s in scenario:
        if s.media_type == "IMAGE" and s.values:  # only card stories
            r = pipe.process(s, s.media_path)
            if r.card_detected:
                latencies.append(r.media_to_result_ms)
    assert len(latencies) > 0
    avg = sum(latencies) / len(latencies)
    p90 = sorted(latencies)[int(len(latencies) * 0.9)] if len(latencies) >= 2 else latencies[0]
    print(f"\n  MEDIA->RESULT avg={avg:.0f}ms p90={p90:.0f}ms (n={len(latencies)})")
    assert avg < 30000, "average latency should be under 30s"


def test_vision_failure_fallback(config, scenario):
    """Rule 70: if Vision fails, the pipeline should still produce results."""
    pipe = PuzzlePipeline(config)
    pipe.vision = None  # simulate vision unavailable
    pipe.process(scenario[1], scenario[1].media_path)
    fe = pipe.fast_entry()
    assert fe["number"]["parts"][0] == "4532", "pipeline should work without vision"


def test_competition_scenario_end_to_end(config, scenario):
    """Rule 83: full success criteria — detect, align, diff, OCR, notify, copy."""
    pipe = PuzzlePipeline(config)
    total_notif = 0
    for s in scenario:
        r = pipe.process(s, s.media_path)
        total_notif += r.notifications
    fe = pipe.fast_entry()
    # criteria checks
    assert not fe["number"]["partial"], "number should be complete"
    assert fe["number"]["display"] == "4532 8841 9023 5678"
    assert fe["cvv"]["display"] == "123"
    assert total_notif > 0, "notifications should fire on new info"
    assert fe["number"]["clipboard"] == "4532884190235678"
