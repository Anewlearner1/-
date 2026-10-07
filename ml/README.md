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

---

# M3: forehand / backhand classification (rule-based baseline)

**Status: implemented as a transparent rule baseline; completely
unvalidated on real footage. No accuracy number exists or should be
quoted.** The synthetic tests check logic only.

## What's implemented

- `ml/stroke_classification.py`
  - `classify_shot(seq, event, hand=None) -> StrokeClassification` and
    `classify_shots(seq, events, hand=None, classifier=classify_shot)`.
  - Output label in `forehand | backhand | other | unknown`, plus
    `confidence` (heuristic, not a probability), `margin`, and the raw
    signals (`takeback_lateral`, `contact_lateral`, `wrist_above_head`,
    `wrist_above_hip`, `takeback_frame`, `hand`, `hand_source`,
    `used_tiebreak`, `reason`) for debugging.
  - Rule per `docs/domain-standards.md` section 3: the primary signal is
    which side of the torso the racket wrist is on at the takeback (frame in
    the 0.6 s before contact farthest from the contact position), signed
    toward the racket-hand side, in torso lengths: racket side = forehand,
    crossed over = backhand. The contact-frame side is a tie-breaker only.
    Not used: which wrist peaked (`ShotEvent.wrist` is ignored), stance
    angle, swing shape/slice.
  - Racket hand: `hand=` override, else the wrist with the higher *peak*
    speed over the clip (peak, not path length, as in the sibling
    `detect_handedness`), resolved once per clip. Confidence is halved when
    the two peaks are within 15% (e.g. two-handed backhand clips).
  - Missing/NaN/low-visibility landmarks, edge-on shoulders, or no valid
    backswing frames give `unknown` (confidence 0), never a guess.
  - The `classifier=` hook keeps the interface swappable for a learned model.
- `ml/eval_stroke_classification.py`: confusion matrix, accuracy, FH-vs-BH
  accuracy and coverage; runs on `tests/fixtures/stroke_eval_labels.json`
  (hand-made, `"source": "synthetic"`, prints a NOT-REAL-FOOTAGE banner).

## Interface the evaluation expects

Additive, optional fields on the existing label JSON (`contact_frames` is
unchanged):

```json
{
  "video_id": "clip_0007", "fps": 30.0,
  "contact_frames": [42, 118, 203],
  "stroke_labels": ["forehand", "backhand", "other"],
  "racket_hand": "right",
  "source": "real"
}
```

- `stroke_labels`: aligned to `contact_frames`; `forehand|backhand|other`
  (`other` = serve/overhead) or `null` for unlabeled. Clips without it are
  skipped. Two-handed vs one-handed is still `backhand` (domain section 3).
- `racket_hand` (optional) and `source` (`real` or `synthetic`; anything but
  `real` is bannered as not-real).
- Landmarks: `<landmarks-dir>/<video_id>.npz` (see `save_landmarks`) or
  `--videos-dir` with `<video_id>.mp4`.
- Run: `python3 -m ml.eval_stroke_classification labels/*.json
  --landmarks-dir lm/ [--mode oracle|detected]`. `oracle` classifies at the
  labeled contact frames (classifier only); `detected` runs `detect_shots`
  and matches within `--tolerance` frames.

**For data-labeler:** `labeling/` does not emit `stroke_labels` (or
`racket_hand`/`source`) yet and will need a matching field. This was not
edited here (not ml-engineer's code).

## Blocked / unvalidated

1. **No real labeled clips with stroke labels** - accuracy on real footage
   is unknown; all thresholds (0.15 torso-length margin, 0.6 s window,
   serve gates 0.3 above head / 1.3 above hip copied from the sibling's 3D
   values) are untuned starting points.
2. **2D, camera-angle dependent.** The sibling measures torso rotation with
   3D world landmarks; here only 2D pixels exist, so rotation is not
   measured. Stand-ins (shoulder L/R ordering fixing the racket side, wrist
   offset from the torso midline) work for front/rear views, degrade toward
   side-on (edge-on shoulders return `unknown`), and are affected by
   perspective, camera roll and a player rotating through 90 degrees.
3. **Racket-hand inference** from peak pixel speed can be wrong (off hand
   faster, two-handed backhand-only clips); pass `hand=` when known.
4. **No ball-tracking cross-check** (M4/M5, GPU unavailable).
5. **Serve/overhead scope is open (product-manager).** The `other` class
   (racket wrist high at contact) is an interim design, not a decision.
6. The takeback frame is "farthest from contact within the window", an
   untested proxy; abbreviated swings fall to the tie-breaker or `unknown`.

## M2 evaluation script (added 2026-10-07)

`python -m ml.eval_shot_timing <label.json ...> --videos-dir data/videos`
runs `extract_player_landmarks()` + `detect_shots()` on the video whose file
name starts with the label's `video_id` and compares against `contact_frames`.
Reports `offset = detected - truth` (negative = detector fired early) and the
hit rate within +/-5 and +/-10 frames. When the sibling `.meta.json` says
`"complete": false` it does not report precision, because unlisted detections
may be real strokes nobody labeled.

First real numbers (6 owner-confirmed contacts in 3 clips, partial labels):
every contact had a detection within 8 frames; mean offset -3.8 frames in the
default mode (5 of 6 exactly 3 frames early), so the wrist-speed peak tends to
precede the contact frame the owner reads. n=6, one footage type, frame-number
base unverified: a lead, not a calibration. See `docs/real-footage-findings.md`.

**Correction (2026-10-07):** the "-3.8 frame mean offset / wrist peak precedes
contact" note above is most likely an artefact: the owner's frame numbers came
from a media player that numbers frames about 3 higher than OpenCV's decoder on
these clips. See `docs/real-footage-findings.md` ("RESOLUTION").
