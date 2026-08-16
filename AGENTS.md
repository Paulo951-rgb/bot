# Story Puzzle Solver — Project Memory

## Status: COMPLETE + HARDENED — 49/49 tests pass, competition-test OK. See "Honest limitations" below.

## Environment (runtime)
- OS: Linux (Debian trixie), x86_64. NOT Windows — target deployment is Windows, but dev/test runs here.
- Python 3.13.14 at /usr/local/bin/python (also `python3`).
- Node v22.23.2 / npm 10.9.8 available.
- Installed via sudo apt: tesseract-ocr 5.5.0 (eng + osd langs), ffmpeg 7.1.5.
- Installed via pip: opencv-python-headless, numpy, pillow, scipy, pytesseract, imageio, pytest.
- No GPU. CPU-only. All vision code must assume CPU.
- Disk: ~25GB free at /workspace.

## Project state
- Repository at /workspace/project was EMPTY at start (no commits, no code, no reference images).
- The spec mentions "two reference images provided" — they were NOT present. Per spec §52-53 we generated
  synthetic fixtures ourselves. A synthetic card generator is the test surface.

## Stack decision
- Pure Python backend using stdlib http.server (NOT FastAPI — switched to zero-dependency local server)
  + vanilla HTML/JS dashboard. No Electron. Windows desktop target is achieved by running the local
  web dashboard + opening in browser; clipboard via server-side copy or browser.

## Architecture (final)
story_puzzle_solver/ package with modules: app (server+CLI), card (detector/template/aligner),
clipboard, common (logger/metrics/timing), config, diff, fusion, media, notification, ocr
(provider+engine+temporal), performance (cache+race), pipeline.py (orchestrator), simulation
(fixture_generator+card_generator), source (base+simulation+authorized), state, video, vision.

## Key learnings / gotchas
- **Tesseract thread thrashing**: running multiple tesseract instances concurrently causes 10x
  slowdown due to OpenMP thread contention. FIX: set OMP_THREAD_LIMIT=1 + OMP_NUM_THREADS=1 in
  TesseractProvider.__init__, then use OCR_WORKERS=2 for real parallelism. (story_4: 11s -> 1s)
- **Card detector corner ordering**: use sum/difference method (TL=min x+y, BR=max x+y,
  TR=max x-y, BL=min x-y) for stable homography. _quad_from_contour uses min-area rotated rect.
- **OCR reliability**: inverted threshold variant + reject conf==0 garbage + early-stop selection.
  Digit whitelist enforced post-OCR. Never invent (rule 2): keep '?' for ambiguous.
- **Video _prev_card**: only set _prev_card from the BEST video frame (set_prev=False during scan),
  otherwise intermediate frames corrupt the diff state for the next story.
- **numpy bool in JSON**: cast card_detected to native bool() before JSON serialization.
- **State load**: load PuzzleState in __init__ (not just prewarm) for restart recovery (rule 51).
- **Vision null-safe**: guard `if self.vision is not None and self.vision.available()`.
- **Perspective warp crop**: after warpPerspective, crop card to recomputed bounding box to avoid
  shape mismatch on paste.

## Performance (final, CPU-only)
- story_1 (image, 1 region): 319ms media->result
- story_2 (image, 2 regions): 958ms
- story_3 (video, early-exit): 2.4s
- story_4 (image, full reveal): 1032ms
- Decoys: <50ms (skipped, no card)
- Pipeline p50: 958ms, p90: 1837ms

## Commands
- `npm run start` / `python -m story_puzzle_solver.app.cli start --simulation` — dashboard
- `npm run check` — dependency check
- `npm run test` / `python -m pytest tests/ -v` — 49 tests
- `npm run test:simulation` — full simulation
- `npm run competition-test` — D-day test (rule 81)
- `npm run reference-image-test` — card detection on user images in fixtures/reference-images/ (§22)
- `npm run health-check:windows` / `setup:windows` — Windows setup + health check scripts

## Hardening work (V3 finalization)
- **VisionEngine** now honestly reports UNAVAILABLE (no backend). available()=False; recognize_region
  returns available=False. Previously it faked confidence=0 presented as analysis — FIXED (rule 10).
- **Notifications**: idempotency via event_id (sha1 of kind|region|value); duplicates suppressed.
  Detailed payload (type/value-masked/confidence%/latency). notification_latency_ms bug fixed
  (was now_ms()-now_ms()=0). _mask() hides full values in toast body (rule 15, 42).
- **Clipboard Windows**: uses PowerShell Set-Clipboard (UTF-8, no trailing newline) instead of `clip`
  (which mangles non-ASCII + adds newline). Linux fallback (xclip/xsel/pbcopy) unchanged.
- **Source status**: pipeline tracks source_status (SIMULATION/AUTHORIZED/DISCONNECTED/SOURCE_UNAVAILABLE).
  Dashboard shows it honestly — never CONNECTED without a real authorized source (rule 24). cmd_start
  prints SOURCE= clearly. AuthorizedStorySourceAdapter base class added (source/authorized_adapter.py)
  with honest connect() (requires authorize() first).
- **Metrics**: added state_latency_ms (save_state timing), download_latency_ms (recorded in poll loop),
  media_to_result_ms added to STAGES tuple.
- **Windows scripts**: scripts/setup_windows.ps1 + scripts/health_check_windows.ps1 ([OK]/[FAIL] report).
- **reference-image-test** CLI command: tests detector on user images, never invents content.

## Honest limitations (NOT fully operational for real surveillance)
- AuthorizedStorySource: interface + adapter only — NO real implementation. Surveillance of real
  stories impossible until a legitimate authorized source is plugged in (rule 7, §24).
- Notifications + clipboard: code written but NOT validated on real Windows (Linux dev env).
- VisionEngine: honestly UNAVAILABLE (no backend) — pipeline runs OCR+Diff only (rule 70).
- Reference images absent from repo — detector validated only on synthetic card.

## Final result
- NUMBER: 4532 8841 9023 5678 (complete, conf 0.87-0.96)
- NAME: BENOITCHEVALIER (OCR no space), EXP: 08/31 (now complete!), CVV: 123
- 9 notifications on new info, 0 on duplicates (state dedup works)

## Spec §3 BIS (card layout)
- Vertical ROI layout: CARDHOLDER_NAME (y=0.30) -> CARD_NUMBER (y=0.46) -> EXPIRATION+CVC (y=0.70)
- Expiration and CVC are BELOW the number (side by side), NOT to the right of it
- Semantic labels added to CardRegionSpec + FIELD_TO_REGIONS mapping in card/template.py
- config/puzzle_initial_state.json: user seeds known values, never invented (null=UNKNOWN)
- Pipeline loads initial state only when no persisted .state exists (restart recovers full state)
- Reorganizing layout fixed EXP extraction: 8/3: (partial) -> 08/31 (complete)

## Security note
- StorySource is an abstraction. SimulationStorySource is the only concrete source implemented and used
  for all tests. AuthorizedStorySource is an interface only — real authorized access must be plugged in
  by the user. NO cookie theft / session hijack / captcha bypass / anti-bot bypass is implemented.
