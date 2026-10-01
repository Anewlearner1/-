# Backend — upload quality gate (M0)

Status: M0 prep work, done ahead of M1 per product-manager's request. See
`docs/technical-plan.md` §5 for the input-spec requirements this
implements, and §6 for milestones.

## What this is

`upload_quality.py` is the quality gate that should run right after a
video is uploaded and *before* it's queued into the batch-processing
pipeline (§2: offline batch, upload → background compute → output). It's a
standalone module with no batch-processing job queue / GPU scheduler
wiring yet (that's separate, still-to-be-built backend-engineer work) —
but it now has a thin JSON API wrapper (below): `check_upload_quality()`
is the function that wrapper calls first.

## Upload API endpoint

`backend/api/upload.py` is a small FastAPI app exposing
`POST /upload` (multipart file upload). It saves the file under
`data/uploads/`, runs `check_upload_quality()`, and returns the
`QualityReport` as JSON with the *exact* field names ui-ux-designer's
`design/upload-flow.md` already documents against this module
(`passed`, `checks[].name/status/message_zh/detail/metrics`,
`messages_zh`) — that doc is the spec for what the frontend does with
each field/status value; read it before changing this response shape.

- `passed: true` responses also include `upload_id`, a placeholder string
  id (`_fake_enqueue()` in `upload.py`) so the frontend has something to
  show on the "已送出處理" screen. **This is not a real job queue** — no
  GPU scheduler/queue exists yet — and the stub is commented as such at
  its definition; swap it for a real enqueue call once the queue exists.
- A malformed/unreadable file (`ValueError` from `check_upload_quality`)
  returns HTTP 422 with a *different* JSON shape
  (`{"error": "invalid_video_file", "detail": ...}`), not a `QualityReport`
  — see design doc §4 on why the frontend needs to tell these apart.
- Tests: `tests/test_upload_api.py`, using FastAPI's `TestClient` (no
  server process needed) and the same synthetic-video fixtures as
  `test_upload_quality.py`.

## What's real vs. placeholder

| Check | Status | Notes |
|---|---|---|
| FPS >= 60 | **Real** | Reads `CAP_PROP_FPS` from the container via OpenCV. Below 60fps -> FAIL with a re-shoot message citing the ~1m/frame ball blur from §5. |
| Camera stability (fixed/tripod) | **Real, but a heuristic** | Estimates frame-to-frame global displacement via `cv2.phaseCorrelate` on a sampled subset of frames, and (as of the Finding 1 fix, see `docs/qa-findings.md`) also tracks the cumulative, directionally-consistent drift across the sampled sequence so a slow, smooth pan is now flagged too, not just high-frequency jitter. This is still a cheap heuristic screen, **not** a certified tripod detector: it can be fooled by a very steady handheld shot, a large subject near the lens, or a pan that reverses direction partway through the clip (the directional drift partially cancels). Thresholds (`MAX_JITTER_PIXELS`, `MAX_JITTER_STD_PIXELS`, `MAX_CUMULATIVE_DRIFT_PIXELS`, `MIN_PAN_DIRECTIONAL_CONSISTENCY`) are hand-picked, not calibrated against labeled footage. Full honesty notes are in the module docstring above `_estimate_camera_jitter`. |
| Court corners visible (all 4) | **Placeholder / stub only** | This is explicitly **cv-engineer's** work: court keypoint detection + homography, milestone M4 in `docs/technical-plan.md` §6, which doesn't exist yet. `upload_quality.py` defines a `CourtCornerChecker` abstract interface and ships `NotImplementedCourtCornerChecker`, which always returns `CheckStatus.NOT_IMPLEMENTED` and a Chinese message telling the uploader to self-check the framing. **It never returns a fake PASS.** |

## Dependency on cv-engineer (M4)

Once cv-engineer's court-corner detector exists, wire it in by
implementing `CourtCornerChecker.check()` and passing that instance as
`court_corner_checker=` to `check_upload_quality()` — no caller-side
changes needed elsewhere (upload handler, queue, etc.), since the
interface and the `QualityReport.court_corners` field don't change. Until
that lands:

- `QualityReport.passed` treats `NOT_IMPLEMENTED` as non-blocking (an
  upload isn't stuck forever waiting on unbuilt CV work), but a real
  `FAIL` from a plugged-in checker *does* block, same as any other check.
- The frontend should still show the stub's `message_zh` so the uploader
  self-checks framing manually in the meantime.

## Result shape

`check_upload_quality(video_path, court_corner_checker=None) -> QualityReport`

- `QualityReport.passed`: bool, safe to enqueue.
- `QualityReport.checks`: `[fps, camera_stability, court_corners]`, each a
  `CheckResult(name, status, detail, message_zh, metrics)`.
- `QualityReport.messages_zh`: ordered list of Traditional Chinese
  re-shoot prompts for every check that isn't a clean PASS — meant to be
  surfaced directly by frontend-engineer's upload flow.

## Tests

`tests/test_upload_quality.py` + `tests/synth.py` (synthetic mp4
generation via `cv2.VideoWriter`, following the *pattern* used in the
sibling `tennis-form-coach` project's `tests/test_cli.py` — no code or
domain logic shared between the two projects). Covers: fps pass/fail,
camera-stability pass/fail (static/textured vs. simulated jitter), the
stub court-corner check never passing, pluggability of a real checker,
and that a not-implemented court check alone doesn't block upload.

Run with:

```
cd rally-ai
pip install -r requirements.txt
python3 -m pytest -q
```

9/9 tests pass as of this writing (skips gracefully to a Chinese-language
message if the environment lacks an mp4 encoder). Plus 3 more in
`tests/test_upload_api.py` covering the API endpoint (pass, fps FAIL,
malformed file) — 29/29 total across the repo as of this writing.
