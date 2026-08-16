"""End-to-end puzzle pipeline orchestrator (rules 1, 45-47, 73).

This ties together: StorySource -> MediaDetector -> (Image|Video) pipeline ->
CardDetector -> Aligner -> {Diff, OCR, Vision in parallel} -> Fusion ->
PuzzleState -> Notification -> Clipboard-ready FastEntry.

It implements:
  - FAST / MEDIUM / DEEP levels (rule 46): DEEP never blocks FAST.
  - Parallel DIFF + OCR + Vision after card detection (rule 45).
  - Race engine for the first reliable result (rule 47).
  - Cache (rule 48) + prewarming hooks (rule 49).
  - Robustness: timeouts, retries, fallbacks (rule 50).
  - Early exit for video (rule 13).
"""
from __future__ import annotations

import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import cv2
import numpy as np

from .card import CardAligner, CardDetector, CardTemplate
from .clipboard import ClipboardEngine, FieldValue
from .common.logger import JsonLogger
from .common.metrics import get_metrics
from .common.timing import Timer, now_ms
from .config import Config, RunMode
from .diff import ImageDiffEngine
from .fusion import FusionInput, PuzzleUpdate, ResultFusionEngine
from .media import MediaDetector, MediaInfo
from .notification import WindowsNotificationManager
from .ocr import OCREngine, TemporalOCR, TemporalObservation
from .performance import AnalysisCache, RaceEngine
from .source import StoryItem
from .state import PuzzleState
from .video import VideoFrameEngine
from .vision import VisionEngine


@dataclass
class ProcessResult:
    story_id: str
    media_kind: str
    card_detected: bool
    updates: List[PuzzleUpdate] = field(default_factory=list)
    notifications: int = 0
    total_latency_ms: float = 0.0
    media_to_result_ms: float = 0.0
    error: Optional[str] = None
    debug_images: Dict[str, np.ndarray] = field(default_factory=dict)


class PuzzlePipeline:
    def __init__(self, config: Config, logger: Optional[JsonLogger] = None):
        self.config = config
        self._logger = logger or JsonLogger("pipeline", config.log_dir, config.debug_mode)
        self.metrics = get_metrics()

        # components (prewarmable)
        self.template = CardTemplate()
        self.detector = CardDetector(self.template, logger=self._logger)
        self.aligner = CardAligner(config.card_width, config.card_height, logger=self._logger)
        self.diff_engine = ImageDiffEngine(self.template, logger=self._logger)
        self.ocr_engine = OCREngine(
            confidence_high=config.ocr_confidence_high,
            confidence_medium=config.ocr_confidence_medium,
            digit_whitelist=config.ocr_digit_whitelist,
            logger=self._logger,
        )
        self.temporal_ocr = TemporalOCR(min_confirmations=config.ocr_min_confirmations)
        self.vision = VisionEngine(enabled=config.vision_enabled, logger=self._logger)
        self.fusion = ResultFusionEngine(notify_min_confidence=config.notify_min_confidence,
                                         logger=self._logger)
        self.media_detector = MediaDetector(logger=self._logger)
        self.video_engine = VideoFrameEngine(
            initial_sample_ms=config.video_initial_sample_ms,
            focused_sample_ms=config.video_focused_sample_ms,
            max_frames=config.video_max_frames, logger=self._logger,
        )
        self.notifications = WindowsNotificationManager(
            enabled=config.windows_notifications, logger=self._logger,
            history_path=config.state_dir / "notifications.json",
        )
        self.clipboard = ClipboardEngine(logger=self._logger)
        self.cache = AnalysisCache(enabled=config.cache_enabled)
        self.race = RaceEngine(max_workers=max(config.ocr_workers, 2), logger=self._logger)

        # state
        self.state = PuzzleState()
        self._load_state()  # rule 51: recover state on restart
        self._prev_card: Optional[np.ndarray] = None
        self._prev_values: Dict[str, str] = {}
        self._executor = ThreadPoolExecutor(max_workers=max(config.ocr_workers, 2),
                                            thread_name_prefix="sps-ocr")
        self._media_to_result_t0: Optional[float] = None

    def _load_state(self) -> None:
        state_path = self.config.state_dir / "puzzle_state.json"
        if state_path.exists():
            try:
                self.state = PuzzleState.load(state_path)
                self._logger.info("prewarm_state_loaded")
            except Exception as e:
                self._logger.warn("prewarm_state_load_failed", error=str(e))
        else:
            # no persisted state yet: seed from user-provided initial state (spec §3 BIS)
            initial_path = self.config.project_root / "config" / "puzzle_initial_state.json"
            if initial_path.exists():
                try:
                    self.state = PuzzleState.load_initial(initial_path)
                    n_known = sum(1 for e in self.state.regions.values() if e.value)
                    self._logger.info("initial_state_loaded", known_regions=n_known)
                except Exception as e:
                    self._logger.warn("initial_state_load_failed", error=str(e))

    # ------------------------------------------------------------------
    # Prewarming (rule 49)
    # ------------------------------------------------------------------
    def prewarm(self) -> None:
        self._logger.info("prewarm_start")
        # state already loaded in __init__ (rule 51)
        # touch OCR so models/jit are warm
        dummy = np.full((64, 200, 3), 255, np.uint8)
        if self.ocr_engine.available():
            self.ocr_engine.recognize_region(dummy, digit_mode=True, early_stop=False)
        # test notification
        self.notifications.notify("Story Puzzle Solver prêt", "Surveillance active.", kind="info")
        self._logger.info("prewarm_done")

    def save_state(self) -> None:
        self.config.ensure_dirs()
        self.state.save(self.config.state_dir / "puzzle_state.json")
        self.template.save(self.config.state_dir / "card_template.json")

    # ------------------------------------------------------------------
    # Main entry: process a story item
    # ------------------------------------------------------------------
    def process(self, story: StoryItem, media_path: Path) -> ProcessResult:
        t_start = now_ms()
        self._media_to_result_t0 = now_ms()
        result = ProcessResult(story_id=story.story_id, media_kind="UNKNOWN",
                               card_detected=False)
        try:
            # Media analysis + dedup
            with Timer() as t_det:
                info = self.media_detector.analyze(media_path, story.story_id)
                self.metrics.record("detection_latency_ms", t_det.elapsed_ms)
                dedup = self.media_detector.check_dedup(info)
                if dedup.is_known:
                    self._logger.info("cache_hit_full", story=story.story_id)
                    result.media_kind = info.kind.value
                    result.total_latency_ms = now_ms() - t_start
                    return result
                self.media_detector.remember(info)
                result.media_kind = info.kind.value

            if info.kind.value == "IMAGE":
                self._process_image(info, result, t_start)
            elif info.kind.value == "VIDEO":
                self._process_video(info, result, t_start)
            else:
                self._logger.warn("media_unknown", story=story.story_id)
        except Exception as e:
            self._logger.error("process_error", story=story.story_id, error=str(e),
                              traceback=traceback.format_exc(limit=3))
            result.error = str(e)
        result.total_latency_ms = now_ms() - t_start
        self.metrics.record("total_latency_ms", result.total_latency_ms)
        self.save_state()
        return result

    # ------------------------------------------------------------------
    # IMAGE pipeline (rule 10)
    # ------------------------------------------------------------------
    def _process_image(self, info: MediaInfo, result: ProcessResult, t_start: float) -> None:
        with Timer() as t_dec:
            img = cv2.imread(str(info.path))
        self.metrics.record("decode_latency_ms", t_dec.elapsed_ms)
        if img is None:
            result.error = "decode_failed"
            return
        result.debug_images["original"] = img

        with Timer() as t_cd:
            det = self.detector.detect(img)
        self.metrics.record("card_detection_latency_ms", t_cd.elapsed_ms)
        result.card_detected = bool(det.detected)
        if not det.detected:
            self._logger.info("no_card", story=info.media_id)
            return

        with Timer() as t_al:
            nc = self.aligner.align(img, det)
        self.metrics.record("alignment_latency_ms", t_al.elapsed_ms)
        if nc is None or not nc.success:
            self._logger.warn("align_failed", story=info.media_id)
            return
        result.debug_images["aligned"] = nc.image

        self._analyze_card(nc.image, result, info.media_id, source_frame=-1, t_start=t_start)

    # ------------------------------------------------------------------
    # VIDEO pipeline (rule 11, 12, 13)
    # ------------------------------------------------------------------
    def _process_video(self, info: MediaInfo, result: ProcessResult, t_start: float) -> None:
        # wire the full CardDetector into the video engine probe
        def probe(frame):
            d = self.detector.detect(frame)
            return d.detected, d.confidence, d.bounding_box
        self.video_engine._probe = probe

        early_exit_holder: List[bool] = [False]
        best_card: List[Optional[np.ndarray]] = [None]
        best_quality: List[float] = [0.0]

        def on_frame(cand):
            if early_exit_holder[0]:
                return
            with Timer() as t_al:
                nc = self.aligner.align(cand.image,
                                        self._detection_from_box(cand.image, cand.card_box))
            if nc is None or not nc.success:
                return
            self.metrics.record("alignment_latency_ms", t_al.elapsed_ms)
            # track best-quality card; analyze each but only the best becomes prev
            q = float(getattr(cand, "quality_score", None) or
                      getattr(getattr(cand, "quality", None), "score", 0.0) or 0.5)
            if q > best_quality[0]:
                best_quality[0] = q
                best_card[0] = nc.image
            self._analyze_card(nc.image, result, info.media_id,
                              source_frame=cand.frame_index, t_start=t_start,
                              early_exit_cb=lambda: early_exit_holder.__setitem__(0, True),
                              set_prev=False)
            # EARLY EXIT (rule 13): NEWLY_REVEALED HIGH-confidence -> stop
            for u in result.updates:
                if (u.status == "NEWLY_REVEALED" and u.confidence >= self.config.ocr_confidence_high
                        and u.is_new_info and "?" not in (u.value or "?")):
                    early_exit_holder[0] = True
                    return

        with Timer() as t_video:
            self.video_engine.best_candidates(info.path, k=5, on_frame=on_frame)
        self.metrics.record("card_detection_latency_ms", t_video.elapsed_ms)
        result.card_detected = bool(len(result.updates) > 0)
        # only the best video frame becomes the previous card for next story
        if best_card[0] is not None:
            self._prev_card = best_card[0]

    def _detection_from_box(self, frame: np.ndarray, box):
        """Build a minimal CardDetection object for the aligner from a box."""
        from .card.detector import CardDetection
        if box is None:
            return CardDetection(detected=False, confidence=0.0)
        x, y, w, h = box
        corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        return CardDetection(detected=True, confidence=0.5,
                             bounding_box=box, corners=corners)

    # ------------------------------------------------------------------
    # Analyze a normalized card: parallel DIFF + OCR (+ Vision) -> fusion
    # ------------------------------------------------------------------
    def _analyze_card(self, normalized: np.ndarray, result: ProcessResult,
                      media_id: str, source_frame: int, t_start: float,
                      early_exit_cb: Optional[Callable] = None,
                      set_prev: bool = True) -> None:
        # DIFF (FAST level)
        with Timer() as t_diff:
            diff = self.diff_engine.diff(self._prev_card, normalized)
        self.metrics.record("diff_latency_ms", t_diff.elapsed_ms)

        # OCR + Vision in parallel (rule 45). FAST/MEDIUM split: run OCR on
        # changed/non-masked regions only to save time; DEEP (vision) optional.
        regions_to_ocr = []
        for key, spec in self.template.regions.items():
            rd = diff.regions.get(key)
            if rd is None:
                continue
            if rd.is_masked:
                continue  # skip OCR on masks (FAST)
            regions_to_ocr.append((key, spec, rd))

        def ocr_task(key, spec):
            nc_obj = NormalizedCard(image=normalized, width=self.config.card_width,
                                    height=self.config.card_height,
                                    corners_used=[], method="homography", success=True)
            roi = self.aligner.extract_region(nc_obj, spec.rect)
            r = self.ocr_engine.recognize_region(roi, digit_mode=(spec.kind == "digits"))
            return r

        # Build a NormalizedCard-shaped object for region extraction
        from .card.alignment import NormalizedCard
        nc_obj = NormalizedCard(image=normalized, width=self.config.card_width,
                                height=self.config.card_height,
                                corners_used=[], method="homography", success=True)

        ocr_results: Dict[str, object] = {}
        with Timer() as t_ocr:
            if self.ocr_engine.available() and regions_to_ocr:
                futures = {}
                for key, spec, _ in regions_to_ocr:
                    roi = self.aligner.extract_region(nc_obj, spec.rect)
                    is_digit = (spec.kind == "digits")
                    # text regions: cap variants + deadline so they never block
                    mx = None if is_digit else 3
                    dl = None if is_digit else 4000.0
                    futures[key] = self._executor.submit(
                        self.ocr_engine.recognize_region, roi,
                        digit_mode=is_digit, max_variants=mx, deadline_ms=dl)
                for key, fut in futures.items():
                    try:
                        ocr_results[key] = fut.result(timeout=20)
                    except Exception as e:
                        self._logger.warn("ocr_future_error", region=key, error=str(e))
                        ocr_results[key] = None
        self.metrics.record("ocr_latency_ms", t_ocr.elapsed_ms)

        # Vision (DEEP, optional, never blocks — rule 70: null-safe)
        vision_results: Dict[str, object] = {}
        with Timer() as t_vis:
            if self.vision is not None and self.vision.available():
                for key, spec, _ in regions_to_ocr:
                    vision_results[key] = self.vision.recognize_region(
                        self.aligner.extract_region(nc_obj, spec.rect),
                        digit_mode=(spec.kind == "digits"))
        self.metrics.record("vision_latency_ms", t_vis.elapsed_ms)

        # Fusion per region
        with Timer() as t_fus:
            prev_was_masked_map = {k: (e.status == "MASKED") for k, e in self.state.regions.items()}
            for key, spec, rd in regions_to_ocr:
                ocr_res = ocr_results.get(key)
                vis_res = vision_results.get(key)
                fi = FusionInput(
                    region_key=key,
                    ocr_text=(ocr_res.text if ocr_res else ""),
                    ocr_confidence=(ocr_res.confidence if ocr_res else 0.0),
                    ocr_provider=(ocr_res.variant if ocr_res else ""),
                    diff_score=rd.diff_score,
                    prev_was_masked=prev_was_masked_map.get(key, self._prev_card is None),
                    vision_text=(vis_res.text if vis_res else ""),
                    vision_confidence=(vis_res.confidence if vis_res else 0.0),
                    frame_quality=0.85,
                    source_story=media_id, source_frame=source_frame,
                )
                # masked regions that became unmasked are reported too
                upd = self.fusion.fuse(fi, self.state)
                if upd.provenance:
                    self.state.update(key, upd.value, upd.confidence, upd.status, upd.provenance)
                result.updates.append(upd)
                # notify
                if self.fusion.should_notify(upd):
                    if upd.is_correction:
                        old = self._prev_values.get(key, "?")
                        self.notifications.notify_correction(key, old, upd.value or "")
                    else:
                        self.notifications.notify_new_info(key)
                    result.notifications += 1
                    self.metrics.record("notification_latency_ms", now_ms() - now_ms())
        self.metrics.record("fusion_latency_ms", t_fus.elapsed_ms)

        # also record masked regions (no OCR)
        for key, spec in self.template.regions.items():
            rd = diff.regions.get(key)
            if rd is None or not rd.is_masked:
                continue
            if key in [u.region for u in result.updates]:
                continue
            fi = FusionInput(region_key=key, is_masked=True, source_story=media_id,
                             source_frame=source_frame)
            upd = self.fusion.fuse(fi, self.state)
            if upd.provenance:
                self.state.update(key, upd.value, upd.confidence, upd.status, upd.provenance)
            result.updates.append(upd)

        # media -> result latency (rule 44)
        result.media_to_result_ms = now_ms() - (self._media_to_result_t0 or t_start)
        self.metrics.record("media_to_result_ms", result.media_to_result_ms)

        # learn + advance previous card
        if set_prev:
            self._prev_card = normalized
        self._prev_values = {k: (e.value or "") for k, e in self.state.regions.items()}
        self.template.learn_from("", {k: e.status for k, e in self.state.regions.items()
                                      if e.status == "VISIBLE"})

        if early_exit_cb:
            early_exit_cb()

    # ------------------------------------------------------------------
    # Fast entry snapshot (rule 32)
    # ------------------------------------------------------------------
    def fast_entry(self) -> Dict:
        """Build the FAST ENTRY data for the UI."""
        s = self.state
        digit_keys = ["region_01", "region_02", "region_03", "region_04"]
        number_parts = [s.display_value(k) for k in digit_keys]
        number_partial = any(s.is_partial(k) for k in digit_keys) or any(not s.display_value(k) for k in digit_keys)
        return {
            "number": {
                "display": " ".join(p if p else "????" for p in number_parts),
                "clipboard": "".join(p for p in number_parts if p and "?" not in p),
                "partial": number_partial,
                "parts": number_parts,
                "confidence": min((s.regions[k].confidence for k in digit_keys if k in s.regions), default=0.0),
            },
            "name": _field(s, "region_name"),
            "exp": _field(s, "region_exp"),
            "cvv": _field(s, "region_cvv"),
        }


def _field(state: PuzzleState, key: str) -> Dict:
    e = state.regions.get(key)
    val = e.value if e else None
    partial = bool(val and "?" in (val or "")) or e is None or e.status == "MASKED"
    return {
        "display": val if val and not partial else (val or "????"),
        "clipboard": val if (val and "?" not in val) else "",
        "partial": partial,
        "confidence": e.confidence if e else 0.0,
        "status": e.status if e else "UNKNOWN",
    }
