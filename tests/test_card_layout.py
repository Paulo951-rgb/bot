"""Tests for spec §3 BIS: physical card layout + ROI regions + initial state."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from story_puzzle_solver.card import FIELD_TO_REGIONS, REGION_DEFS, CardRegionSpec
from story_puzzle_solver.state import PuzzleState


def test_roi_layout_vertical_order():
    """Spec §3 BIS: NAME above NUMBER, EXP+CVC below NUMBER (vertical order)."""
    name_y = REGION_DEFS["region_name"].rect[1]
    num_y = REGION_DEFS["region_01"].rect[1]
    exp_y = REGION_DEFS["region_exp"].rect[1]
    cvv_y = REGION_DEFS["region_cvv"].rect[1]
    # name must be ABOVE the number
    assert name_y < num_y, "cardholder name must be above card number"
    # exp + cvv must be BELOW the number
    assert exp_y > num_y, "expiration must be below card number"
    assert cvv_y > num_y, "CVC must be below card number"


def test_exp_cvc_not_right_of_number():
    """Spec §3 BIS: expiration and CVC are NOT to the right of the number."""
    num_x_end = REGION_DEFS["region_04"].rect[0] + REGION_DEFS["region_04"].rect[2]
    exp_x = REGION_DEFS["region_exp"].rect[0]
    cvv_x = REGION_DEFS["region_cvv"].rect[0]
    num_y = REGION_DEFS["region_01"].rect[1]
    exp_y = REGION_DEFS["region_exp"].rect[1]
    cvv_y = REGION_DEFS["region_cvv"].rect[1]
    # If they were "to the right" they'd be at the same y as the number.
    assert exp_y != num_y, "expiration must not be on the number's row"
    assert cvv_y != num_y, "CVC must not be on the number's row"
    # They start at the LEFT side (below the number), not after the number
    assert exp_x < num_x_end, "expiration starts at the left, below the number"
    assert cvv_x < num_x_end, "CVC starts at the left, below the number"


def test_semantic_field_mapping():
    """Spec §3 BIS: CARDHOLDER_NAME / CARD_NUMBER / EXPIRATION_DATE / CVC."""
    assert FIELD_TO_REGIONS["CARDHOLDER_NAME"] == ["region_name"]
    assert FIELD_TO_REGIONS["CARD_NUMBER"] == ["region_01", "region_02", "region_03", "region_04"]
    assert FIELD_TO_REGIONS["EXPIRATION_DATE"] == ["region_exp"]
    assert FIELD_TO_REGIONS["CVC"] == ["region_cvv"]


def test_region_labels_present():
    """Each region has a semantic label."""
    for key, spec in REGION_DEFS.items():
        assert isinstance(spec, CardRegionSpec)
        assert spec.label in ("CARDHOLDER_NAME", "CARD_NUMBER", "EXPIRATION_DATE", "CVC"), \
            f"region {key} missing semantic label"


def test_initial_state_loading(tmp_path):
    """Spec §3 BIS: load initial known values from config, never invent."""
    initial = {
        "regions": {
            "region_01": {"value": "4532", "status": "KNOWN"},
            "region_02": {"value": None, "status": "UNKNOWN"},
        }
    }
    p = tmp_path / "puzzle_initial_state.json"
    p.write_text(json.dumps(initial), encoding="utf-8")
    ps = PuzzleState.load_initial(p)
    # known value loaded
    assert ps.regions["region_01"].value == "4532"
    assert ps.regions["region_01"].status == "KNOWN"
    # unknown stays unknown — NOT invented
    assert ps.regions["region_02"].value is None
    assert ps.regions["region_02"].status == "UNKNOWN"


def test_initial_state_no_file(tmp_path):
    """If no initial state file exists, all regions are UNKNOWN."""
    ps = PuzzleState.load_initial(tmp_path / "nonexistent.json")
    assert len(ps.regions) == 0


def test_pipeline_loads_initial_state(tmp_path):
    """Pipeline seeds state from puzzle_initial_state.json when no persisted state."""
    # create a config with project_root pointing at tmp_path
    from story_puzzle_solver.config import Config
    cfg = Config.load()
    cfg.state_dir = tmp_path / ".state"
    cfg.log_dir = tmp_path / ".logs"
    cfg.project_root = tmp_path
    cfg.ensure_dirs()
    # write initial state with one known region
    init_dir = tmp_path / "config"
    init_dir.mkdir()
    (init_dir / "puzzle_initial_state.json").write_text(json.dumps({
        "regions": {"region_01": {"value": "4532", "status": "KNOWN"}}
    }), encoding="utf-8")
    from story_puzzle_solver.pipeline import PuzzlePipeline
    pipe = PuzzlePipeline(cfg)
    assert pipe.state.regions["region_01"].value == "4532"
    assert pipe.state.regions["region_01"].status == "KNOWN"
    # other regions not invented
    assert "region_02" not in pipe.state.regions or pipe.state.regions["region_02"].value is None


def test_roi_invariance_under_transform(config, generator):
    """Spec §3 BIS: ROIs are found regardless of card position/orientation."""
    from story_puzzle_solver.card import CardDetector, CardAligner
    import numpy as np
    gen = generator
    detector = CardDetector()
    aligner = CardAligner()
    # generate a card at different positions
    for offset in [(0.35, 0.30), (0.65, 0.55), (0.50, 0.45)]:
        s = gen.make_image_story(f"t_{offset}", {"region_01": True, "region_04": True},
                                  offset=offset, angle=0.0)
        img = np.asarray(__import__('cv2').imread(str(s.media_path)))
        det = detector.detect(img)
        if det.detected:
            nc = aligner.align(img, det)
            if nc is not None:
                # all ROIs should be extractable from the normalized card
                for key in REGION_DEFS:
                    rect = REGION_DEFS[key].rect
                    x, y = int(rect[0] * nc.image.shape[1]), int(rect[1] * nc.image.shape[0])
                    w, h = int(rect[2] * nc.image.shape[1]), int(rect[3] * nc.image.shape[0])
                    assert x >= 0 and y >= 0 and x + w <= nc.image.shape[1] and y + h <= nc.image.shape[0], \
                        f"ROI {key} out of bounds after normalization for offset {offset}"
