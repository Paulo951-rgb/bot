"""Story + video fixture generation (rules 52-55).

A *StoryFixture* is a single publication: either an image or a video, with a
known ground-truth card state. The :class:`FixtureGenerator` produces a
progressive sequence that simulates the competition scenario (region_01 known
early, new regions revealed over successive stories, the big reveal ~story 4).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .card_generator import (
    REGIONS,
    CardGroundTruth,
    augment_frame,
    place_card_in_frame,
    render_card,
)


@dataclass
class StoryFixture:
    story_id: str
    media_path: Path
    media_type: str  # IMAGE | VIDEO
    values: Dict[str, str] = field(default_factory=dict)
    revealed: Dict[str, bool] = field(default_factory=dict)
    ground_truth: Optional[CardGroundTruth] = None
    duration_s: float = 0.0
    card_visible_range: Optional[Tuple[float, float]] = None  # (start_s, end_s) for video


class FixtureGenerator:
    def __init__(self, root: Path, seed: int = 7):
        self.root = Path(root)
        self.stories_dir = self.root / "stories"
        self.videos_dir = self.root / "videos"
        self.stories_dir.mkdir(parents=True, exist_ok=True)
        self.videos_dir.mkdir(parents=True, exist_ok=True)
        self.rng = random.Random(seed)

    def _base_values(self) -> Dict[str, str]:
        # stable card values for a whole scenario
        return {
            "region_01": "4532",
            "region_02": "8841",
            "region_03": "9023",
            "region_04": "5678",
            "region_name": "BENOIT CHEVALIER",
            "region_exp": "08/31",
            "region_cvv": "123",
        }

    def make_image_story(self, story_id: str, revealed: Dict[str, bool],
                        offset=(0.5, 0.45), angle=0.0, perspective=0.0,
                        aug=True) -> StoryFixture:
        values = self._base_values()
        card, values, revealed = render_card(values=values, revealed=revealed, rng=self.rng)
        frame, gt = place_card_in_frame(card, offset=offset, angle_deg=angle,
                                        perspective=perspective, rng=self.rng)
        if aug:
            frame = augment_frame(frame, self.rng,
                                 brightness=1.0 + self.rng.uniform(-0.15, 0.15),
                                 contrast=1.0 + self.rng.uniform(-0.1, 0.1),
                                 jpeg=self.rng.randint(0, 20))
        gt.values = values
        gt.revealed = revealed
        path = self.stories_dir / f"{story_id}.png"
        cv2.imwrite(str(path), frame)
        return StoryFixture(story_id=story_id, media_path=path, media_type="IMAGE",
                           values=values, revealed=revealed, ground_truth=gt)

    def make_video_story(self, story_id: str, revealed: Dict[str, bool],
                        duration_s: float = 6.0,
                        card_visible: Tuple[float, float] = (1.0, 4.5),
                        fps: int = 15, moving: bool = True) -> StoryFixture:
        values = self._base_values()
        path = self.videos_dir / f"{story_id}.mp4"
        frame_w, frame_h = 1080, 1920
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(path), fourcc, fps, (frame_w, frame_h))

        n_frames = int(duration_s * fps)
        start_f = int(card_visible[0] * fps)
        end_f = int(card_visible[1] * fps)

        gt: Optional[CardGroundTruth] = None
        for fi in range(n_frames):
            t = fi / fps
            bg = np.zeros((frame_h, frame_w, 3), np.uint8)
            for i in range(frame_h):
                bg[i, :] = (int(60 + 20 * i / frame_h), int(80 + 20 * i / frame_h), int(110 + 30 * i / frame_h))
            cv2.rectangle(bg, (0, 0), (frame_w, 90), (30, 30, 30), -1)

            if start_f <= fi <= end_f:
                # card visible; move slightly and tilt
                prog = (fi - start_f) / max(1, end_f - start_f)
                offx = 0.5 + (0.03 * np.sin(prog * 6)) if moving else 0.5
                offy = 0.45 + (0.02 * np.cos(prog * 5)) if moving else 0.45
                angle = -3 + 4 * prog if moving else 0
                card_img, _, _ = render_card(values=values, revealed=revealed, rng=self.rng)
                frame, g = place_card_in_frame(card_img, frame_w=frame_w, frame_h=frame_h,
                                               offset=(offx, offy), angle_deg=angle, rng=self.rng)
                # blur during entry/exit for realism
                if fi in (start_f, end_f):
                    frame = augment_frame(frame, self.rng, blur=5)
                if gt is None or fi == (start_f + end_f) // 2:
                    gt = g
                    gt.values = values
                    gt.revealed = revealed
                writer.write(frame)
            else:
                writer.write(bg)
        writer.release()
        return StoryFixture(story_id=story_id, media_path=path, media_type="VIDEO",
                           values=values, revealed=revealed, ground_truth=gt,
                           duration_s=duration_s, card_visible_range=card_visible)

    def make_non_card_story(self, story_id: str) -> StoryFixture:
        """A decoy story with no card (rule 74)."""
        h, w = 1920, 1080
        img = np.zeros((h, w, 3), np.uint8)
        for i in range(h):
            img[i, :] = (int(50 + 30 * i / h), int(60 + 30 * i / h), int(80 + 40 * i / h))
        cv2.putText(img, "Random moment", (200, 900), cv2.FONT_HERSHEY_DUPLEX, 3, (255, 255, 255), 3)
        cv2.circle(img, (540, 500), 200, (40, 100, 60), -1)
        path = self.stories_dir / f"{story_id}.png"
        cv2.imwrite(str(path), img)
        return StoryFixture(story_id=story_id, media_path=path, media_type="IMAGE",
                           values={}, revealed={}, ground_truth=None)

    # ------------------------------------------------------------------
    # Canonical competition scenario (rule 75, 81)
    # ------------------------------------------------------------------

    def competition_scenario(self) -> List[StoryFixture]:
        """A realistic sequence: decoys, then progressive reveals, big video reveal."""
        fixtures: List[StoryFixture] = []

        fixtures.append(self.make_non_card_story("story_decoy_1"))
        fixtures.append(self.make_image_story("story_1", {"region_01": True},
                                              angle=-4, offset=(0.5, 0.40)))
        fixtures.append(self.make_image_story("story_2", {"region_01": True, "region_04": True},
                                              angle=3, offset=(0.48, 0.44)))
        fixtures.append(self.make_non_card_story("story_decoy_2"))
        # The big reveal: video where region_02 (and region_03) become visible mid-clip
        fixtures.append(self.make_video_story(
            "story_3",
            revealed={"region_01": True, "region_02": True, "region_04": True},
            duration_s=6.0, card_visible=(1.0, 4.5),
        ))
        # final image with everything revealed
        fixtures.append(self.make_image_story("story_4", {
            "region_01": True, "region_02": True,
            "region_03": True, "region_04": True,
            "region_name": True, "region_exp": True, "region_cvv": True,
        }, angle=-2, offset=(0.5, 0.42)))
        return fixtures

    def surprise_scenario(self, seed: Optional[int] = None) -> List[StoryFixture]:
        """Randomized surprise test (rule 82)."""
        rng = random.Random(seed if seed is not None else self.rng.randint(0, 10**6))
        self.rng = rng
        fixtures: List[StoryFixture] = []
        n = rng.randint(3, 6)
        all_keys = list(REGIONS.keys())
        revealed_so_far: Dict[str, bool] = {"region_01": True}
        for i in range(n):
            is_video = rng.random() < 0.5
            # reveal 0-2 new regions
            newly = rng.sample([k for k in all_keys if not revealed_so_far.get(k)],
                              k=rng.randint(0, min(2, len(all_keys) - sum(revealed_so_far.values()))))
            for k in newly:
                revealed_so_far[k] = True
            sid = f"surp_{i}"
            if is_video:
                fixtures.append(self.make_video_story(
                    sid, dict(revealed_so_far),
                    duration_s=rng.uniform(3, 8),
                    card_visible=(rng.uniform(0.5, 2.0), rng.uniform(3.0, 6.5)),
                ))
            else:
                fixtures.append(self.make_image_story(
                    sid, dict(revealed_so_far),
                    angle=rng.uniform(-12, 12),
                    offset=(rng.uniform(0.4, 0.6), rng.uniform(0.38, 0.5)),
                    perspective=rng.choice([0, 0, 0.05, 0.08]),
                ))
        return fixtures

    def generate_brief_card_video(self, path: Path, duration_s: float = 3.0,
                                  card_appear_s: Tuple[float, float] = (1.0, 1.8),
                                  fps: int = 15) -> Path:
        """A short video where the card is visible only briefly (rule 55 TEST 8)."""
        values = self._base_values()
        revealed = {"region_01": True}
        frame_w, frame_h = 1080, 1920
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(path), fourcc, fps, (frame_w, frame_h))
        n_frames = int(duration_s * fps)
        start_f = int(card_appear_s[0] * fps)
        end_f = int(card_appear_s[1] * fps)
        for fi in range(n_frames):
            if start_f <= fi <= end_f:
                card_img, _, _ = render_card(values=values, revealed=revealed, rng=self.rng)
                frame, _ = place_card_in_frame(card_img, frame_w=frame_w, frame_h=frame_h,
                                               offset=(0.5, 0.42), angle_deg=0.0, rng=self.rng)
            else:
                frame = np.zeros((frame_h, frame_w, 3), np.uint8)
                for i in range(frame_h):
                    frame[i, :] = (60 + 20 * i // frame_h, 80 + 20 * i // frame_h, 110 + 30 * i // frame_h)
            writer.write(frame)
        writer.release()
        return path
