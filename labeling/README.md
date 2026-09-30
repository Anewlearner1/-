# labeling/ — semi-automated shot-timing labeling tool

**Status: tooling only. No real video has been labeled with this tool yet.**
Nothing in this directory is a delivered dataset — there is no
`labeling/labels/` folder of finished clips, and `ml/shot_timing.py`'s
accuracy against real footage is still unvalidated (see `ml/README.md`,
"Blocked", item 2). This is the mitigation the technical plan calls for
(`docs/technical-plan.md` §7: labeling cost is a named top risk, and the
explicit mitigation is building semi-automated tooling before labeling by
hand) — building it is the deliverable here, not a labeled clip.

## What it produces

The exact JSON shape ml-engineer specified in `ml/README.md` under
"Interface needed from data-labeler", nothing more and nothing less:

```json
{"video_id": "clip_0007", "fps": 30.0, "contact_frames": [42, 118, 203, 275]}
```

Provenance (how the label was produced — which candidates came from
`detect_shots()`, how many a human confirmed/rejected, who reviewed it and
when) lives in a **separate sibling file**, `<video_id>.meta.json`, next to
the label, so it never changes the label's own shape. Its `"source"` field
is `"detect_shots+human_correction"` for every label this tool produces —
there is currently no fully-manual labeling path, so that is always what
you'll see until one exists.

## Why "semi"-automated

`ml/shot_timing.py::detect_shots()` is already a real, working peak
detector — just unvalidated against real labels. Rather than having a human
mark every contact frame on a blank timeline, this tool runs
`extract_player_landmarks()` + `detect_shots()` first to get *candidate*
contact frames, and has a human confirm, correct, or add to that — the
bootstrap-then-correct workflow the data-labeler role brief calls for.

(The sibling `tennis-form-coach` project has a simpler contact-frame
correction tool for a related purpose sketched as `correct.py` — it was not
actually built there either, so there was nothing to reuse; this tool is a
fresh implementation for Rally AI, written for `PlayerLandmarkSequence` /
`ShotTimingResult`, not imported from that separate codebase.)

## Running it on a real video

Three stages, each a CLI subcommand of `labeling/label_shots.py`:

### 1. Extract candidates

```bash
python -m labeling.label_shots extract path/to/clip_0007.mp4 labeling/sessions/clip_0007
```

This runs `extract_player_landmarks()` + `detect_shots()` on the video and
writes, under the given output directory:

- `candidates.json` — the bootstrapped, **unreviewed** candidate list: one
  entry per `detect_shots()` peak, each with `frame`, `time_s`,
  `peak_speed`, `wrist`, `status: "pending"`, and `image` (path to a still).
- `frames/candidate_NNNNNN.jpg` — one still image per candidate, grabbed
  directly from the source video, with the frame number, timestamp, and
  which wrist burned into the image (and into the filename) so a reviewer
  can tell candidates apart at a glance without a video player. This
  mirrors how frames were manually reviewed in the sibling
  `tennis-form-coach` project.

Pass `--no-images` to skip the still-image dump (e.g. for a quick
detector-only check) and `--video-id` to override the default
(the video's filename stem).

### 2. Human review

**What the reviewer needs to do:** open `frames/` and look at each
candidate still next to the real video around that timestamp, then decide,
per candidate, whether it's a genuine ball-contact frame. Two ways to
record that:

- **Interactive CLI:**

  ```bash
  python -m labeling.label_shots review labeling/sessions/clip_0007/candidates.json
  ```

  Walks every `"pending"` candidate, prints its frame/time/wrist/image
  path, and asks `keep(k) / drop(d) / skip(s)`. At the end it asks for any
  missed contact frames (comma-separated frame numbers, found by scrubbing
  the real video) and appends them as `"status": "added"` entries.

- **Hand-edit `candidates.json` directly:** set each candidate's
  `"status"` to `"confirmed"` (it's a real contact) or `"rejected"` (it's
  not — e.g. a split-step or serve ball-toss, the false-positive failure
  mode `ml/README.md` calls out for wrist-speed-only detection). Append a
  new object with `"status": "added"` for any contact `detect_shots` missed
  (frame, and `time_s = frame / fps`; `peak_speed`/`wrist` can be `null`).

Every bootstrapped candidate must end up `"confirmed"` or `"rejected"` —
`finalize` refuses to run while any are still `"pending"`, precisely so a
label can't accidentally include an unreviewed guess.

### 3. Finalize

```bash
python -m labeling.label_shots finalize labeling/sessions/clip_0007/candidates.json \
    --out labeling/labels/clip_0007.label.json --reviewer "your name"
```

Writes the final label JSON (exact three-key shape above) and the sibling
`clip_0007.meta.json` provenance file (source, reviewer, reviewed-at
timestamp, and counts of confirmed/rejected/added candidates).

## For ml-engineer / qa-engineer consuming labels later

- Only trust a `<video_id>.label.json` if its sibling `.meta.json` exists
  and has `"source": "detect_shots+human_correction"` — that's the only
  provenance this tool currently produces (no fully-manual path exists
  yet). A label file with no matching `.meta.json` next to it wasn't
  produced by this tool and its provenance is unknown.
- `n_rejected` in the meta file is a rough proxy for how often
  `detect_shots()`'s wrist-speed-only heuristic is wrong on that clip
  (false positives per `ml/README.md`'s "Blocked" item 1) — worth tracking
  across clips once real labeling starts.

## Tests

`tests/test_label_shots.py` covers the plumbing — building candidates from
a `detect_shots` result, writing/reading `candidates.json`, the
finalize-refuses-while-pending guard, and an end-to-end
extract → (stubbed) human correction → finalize flow producing a
schema-valid label — using `tests/synth_pose.py`'s synthetic landmark
fixture rather than a real video (no video I/O or mediapipe model download
needed for these tests). The human-correction step in that end-to-end test
is a stub (statuses set directly, as a JSON-editing reviewer would), not
the interactive CLI prompt. These tests confirm the tool's logic is
correct; they are not, and do not claim to be, real labeled data.
