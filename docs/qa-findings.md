# QA findings: upload-quality gate boundary testing

Author: qa-engineer, M2. Scope: `backend/upload_quality.py` +
`backend/api/upload.py`, tested with synthetic mp4s generated via
`cv2.VideoWriter` (see `tests/synth.py`, `tests/test_qa_boundary_cases.py`).
Full run: `python3 -m pytest -q` -> 38 passed (29 pre-existing + 9 new).

`frontend/` exists in the repo tree already (presumably another agent's
concurrent work) -- not touched, not reviewed here.

## Addressed to: cv-engineer, backend-engineer

### Finding 1 (confirmed, not new): slow smooth pan passes camera_stability -- severity: medium, worth prioritizing before the next real-footage pass

`upload_quality.py`'s own module docstring (lines ~224-227) already flags
this as a known limitation of `_check_camera_stability`. I built a real
synthetic video -- a textured scene panning at a constant, smooth
1px/frame (`write_slow_pan_video` in `tests/synth.py`) -- and ran it
through `check_upload_quality`. Confirmed result:

- `camera_stability.status == PASS`
- `mean_displacement_px = 2.12`, `std_displacement_px = 0.70` (both well
  under `MAX_JITTER_PIXELS=4.0` / `MAX_JITTER_STD_PIXELS=6.0`)

This is not a hypothetical: a real tripod operator doing a slow pan
across the court to follow play (a plausible, even likely, real-world
shooting style) will sail through the gate with a camera that is
*not fixed*. Downstream, this feeds a moving-camera clip into whatever
depends on a fixed-camera assumption (ball trajectory triangulation,
court homography stability across frames) without any signal that the
assumption was violated.

**Why this is worth prioritizing before the next real-footage validation
pass**: the gate's entire purpose is to catch bad footage *before* GPU
time is spent (see module docstring, lines 1-8). A failure mode that lets
a specific, plausible real-shooting-style category through silently is
exactly the kind of thing that won't show up until real uploads start
failing downstream in a more expensive and harder-to-diagnose way. It
doesn't need to block M2, but it should be on the list before uploads
from real users/courts are trusted based on this gate passing.

Suggested directions for cv-engineer/backend-engineer to evaluate (not
prescriptive -- I'm QA, not proposing the fix):
- Track cumulative drift across the whole sampled sequence (first frame
  vs. last, or a running sum of per-pair displacement vectors), not just
  per-pair delta -- a pan's per-pair displacement is small but its sum
  over 90 frames is large and directionally consistent, unlike jitter
  which should cancel out.
- Or: flag high *directional consistency* (low variance in displacement
  *vector angle*, not just magnitude) as a separate "sustained pan"
  signal alongside the existing jitter check.

Test reproducing this: `tests/test_qa_boundary_cases.py::test_slow_smooth_pan_incorrectly_passes_camera_stability`.
It's written to keep passing (i.e. keep documenting the gap) until the
heuristic actually changes -- if it starts failing, that means the fix
landed; update this doc rather than just flipping the assertion.

## Other cases tested (no new findings -- documenting for completeness)

| # | Case | Result | Notes |
|---|---|---|---|
| 2 | fps=59 vs fps=60 boundary | Correct: 59 -> FAIL, 60 -> PASS | `MIN_FPS=60.0`, check is `fps < MIN_FPS`; confirmed exact boundary behaves as documented, no off-by-one. |
| 3 | 1-frame / 2-frame video | Correct: both FAIL gracefully (`frame_pairs_analyzed=0` or a real-but-degenerate measurement), no crash. API layer (`/upload`) returns 200 with `passed=False`, not a 500. | Exercises the `n_pairs == 0` path already in the code. |
| 4 | Truncated valid mp4 (first ~10% of bytes, moov atom dropped) via `/upload` | Correct: 422 with `{"error": "invalid_video_file", ...}` shape | Same code path as garbage-bytes case, confirmed with a *real* truncated encode, not just garbage bytes. |
| 4b | Garbage bytes with `.mp4` extension via `/upload` | Correct: 422, same shape | Matches existing `test_upload_api.py` coverage; added as a belt-and-suspenders duplicate in the new boundary-case file. |
| 5 | Large random frame-to-frame jitter (`max_shift=40px`) | Correct: FAIL, well beyond both thresholds | True-positive check: confirms the stability check isn't broken in the "never fails" direction either. |

No crashes, no 500s, no unhandled exceptions found anywhere in this pass.
The fps and corrupted-file boundaries are solid. The one real gap is
Finding 1 above, which is already known/documented in the source -- this
just confirms it with a concrete repro and numbers instead of leaving it
as an unverified docstring claim.
