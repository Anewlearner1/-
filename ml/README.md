# ml/ — M2: shot-timing (contact) detection

Per `docs/technical-plan.md` §3, shot-timing detection is specified as
**ball trajectory direction change + pose swing-speed peak**, combined —
flagged in the plan as having no off-the-shelf solution. This directory
implements **only the pose-swing-speed half**. See "Blocked" below for the
rest.

## What's implemented

- `cv/pose_overlay.py::extract_player_landmarks()` — new function, added for
  this milestone. Reuses the existing model-loading, frame-decoding, and
  player-selection internals from M1's `generate_pose_overlay()` (both now
  share a `_run_pose_pipeline()` core) but returns a per-frame
  `PlayerLandmarkSequence` (pixel-space landmark coordinates + visibility +
  detection flag) instead of rendering a video. `generate_pose_overlay()`'s
  behavior and tests (`tests/test_pose_overlay.py`) are unchanged by this
  refactor.
- `ml/shot_timing.py::detect_shots()` — given a `PlayerLandmarkSequence`,
  computes per-frame wrist speed for **whichever wrist is moving faster**
  (no handedness assumption — same reasoning as `cv/player_selection.py`
  not modeling player identity), smooths it (Savitzky-Golay), and finds
  peaks (`scipy.signal.find_peaks`, prominence + min-separation gated,
  approach adapted in spirit from the sibling `tennis-form-coach` project's
  `tennis_coach/segment.py` but written fresh for this codebase — simpler,
  frame-count-only output, no swing-phase segmentation, no 3D geometry).
  Each accepted peak becomes a `ShotEvent(contact_frame, contact_time_s,
  peak_speed, wrist)`.
- Tests (`tests/test_shot_timing.py` + `tests/synth_pose.py`): a synthetic
  landmark sequence built from normal-CDF position ramps (same technique as
  `tennis-form-coach/tests/synth.py` — a CDF ramp's derivative is an exact
  Gaussian, so the wrist-speed peak lands at a precisely known frame),
  giving an exact, hand-computable ground truth for swing count and contact
  frame. Covers: single/multiple swings, left- vs. right-handed swings, a
  swing's own recovery motion not being double-counted, no-motion clips,
  too-short clips, and a detection-dropout gap not registering as a false
  shot.

## Blocked — not implemented, not validated

1. **Ball-trajectory half of the detection method.** There is no ball
   tracking anywhere in this codebase yet — M1 (`cv/pose_overlay.py`) is
   pose-only. Ball tracking is M4/M5 (TrackNet-based, GPU-dependent,
   explicitly out of scope for this GPU-less sandbox). `detect_shots()`
   today relies on the pose signal alone, which means it **will have false
   positives on any fast arm motion that isn't a stroke** (e.g. a big
   split-step recovery, a serve ball-toss, an emphatic gesture) — nothing
   currently corroborates a wrist-speed peak against an actual ball
   direction reversal. When ball tracking exists, the real M2 detector
   should fuse the two signals (e.g. require a wrist-speed peak to coincide
   with, or closely precede, a ball-trajectory reversal) rather than
   trusting wrist speed alone.

2. **Real-footage accuracy validation against human-labeled shot counts.**
   The plan's stated M2 acceptance criterion is "compare against
   human-labeled shot counts." `data-labeler` has not produced any labels
   yet (no labeling work has started in this repo). The only correctness
   signal in this codebase right now is the synthetic-fixture test suite
   above — it confirms the detector's *logic* is right on an idealized,
   exactly-timed signal, but it is **not** a real-footage accuracy number
   and must not be reported or treated as one. Real accuracy — including
   how much the current false-positive risk from item 1 actually costs in
   practice — is an open, blocked follow-up.

### Interface needed from data-labeler to unblock real accuracy validation

To plug in a real accuracy check once labels exist, the simplest interface
that `ml/shot_timing.py` can consume directly is: for each labeled clip, a
list of contact frame indices (or timestamps, convertible via the clip's
fps) — e.g.

```json
{
  "video_id": "clip_0007",
  "fps": 30.0,
  "contact_frames": [42, 118, 203, 275]
}
```

Given that, an evaluation script here would run `extract_player_landmarks()`
+ `detect_shots()` on the same clip, and compare `result.events` against
`contact_frames` (matched by nearest frame within some tolerance, e.g.
±5 frames / ~150ms) to get precision/recall/shot-count-accuracy, per the
plan's acceptance criterion. That script does not exist yet — it should be
written once the first labeled clips are available, not before.
