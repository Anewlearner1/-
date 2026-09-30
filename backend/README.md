# Backend — upload quality gate (M0)

Status: M0 prep work, done ahead of M1 per product-manager's request. See
`docs/technical-plan.md` §5 for the input-spec requirements this
implements, and §6 for milestones.

## What this is

`upload_quality.py` is the quality gate that should run right after a
video is uploaded and *before* it's queued into the batch-processing
pipeline (§2: offline batch, upload → background compute → output). It's a
standalone module with no queue/API/DB wiring yet — those land when the
upload endpoint and job queue are built, and this module's
`check_upload_quality()` is the function they call first.

## What's real vs. placeholder

| Check | Status | Notes |
|---|---|---|
| FPS >= 60 | **Real** | Reads `CAP_PROP_FPS` from the container via OpenCV. Below 60fps -> FAIL with a re-shoot message citing the ~1m/frame ball blur from §5. |
| Camera stability (fixed/tripod) | **Real, but a heuristic** | Estimates frame-to-frame global displacement via `cv2.phaseCorrelate` on a sampled subset of frames. This is a cheap jitter screen, **not** a certified tripod detector. It will pass a smooth pan and can be fooled by a very steady handheld shot or a large subject near the lens. Thresholds (`MAX_JITTER_PIXELS`, `MAX_JITTER_STD_PIXELS`) are hand-picked, not calibrated against labeled footage. Full honesty notes are in the module docstring above `_estimate_camera_jitter`. |
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
message if the environment lacks an mp4 encoder).
