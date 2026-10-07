# Real-footage findings

Running log of what happened when the pipeline met real video. Each entry
states what was measured, by whom, and what it does NOT show. Nothing here is
an accuracy benchmark: samples are tiny and the footage is off-spec.

## 2026-10-04: first real runs (owner's Windows PC + sandbox)

### Footage
- `IMG_3173.mov`: phone, 1080x1920 portrait, 29fps, 11.8s, shot from behind
  the baseline, two serves. Fails the 60fps gate.
- `clip1`-`clip4`: ATP broadcast match highlights, 854x480, 25fps, 171-319s,
  edited montages (cuts, replays, close-ups). Copyrighted: kept local, never
  committed (`.gitignore` blocks `videos/` and video extensions).
- None of these meet the input spec in `docs/technical-plan.md` §5.

### Upload quality gate (`python -m backend.library scan`)
- All 5 videos fail `fps`. The 4 broadcast clips also fail `camera_stability`.
- The `camera_stability` failure is probably cuts and broadcast pans being read
  as shake; the message text ("handheld") would be misleading for this footage.
  The heuristic cannot tell shake from editing.

### Pose + shot detection (`scan --shots`)
| Video | pose_detection_rate | detections | Known / reviewed truth |
|---|---|---|---|
| IMG_3173 | 0.994 | 14 | 2 serves (approx. frames 46 and 266, found earlier by a different detector and eyeballed; not frame-labeled) |
| clip4 | 0.318 | 26 | **0 of 26 judged real strokes** by the project owner, from stills dumped by `labeling/label_shots.py` |

### What this does and does not show
- Shows: the pipeline runs end to end on a real Windows machine; the pose-speed
  detector over-fires badly (14 vs 2 on phone footage; 0/26 precision on
  broadcast); on 480p broadcast video the target-player stub finds a player in
  only about a third of frames.
- Does not show: recall (missed strokes are unknown); behaviour on spec-compliant
  footage; why the broadcast detections were wrong. Hypotheses, not verified:
  `LargestCentralPlayerSelector` locks onto non-players or switches person at
  cuts, producing fake speed spikes. Needs the stills reviewed for cause.
- Review method caveat: precision was judged on single stills at the speed-peak
  frame, which can sit a few frames from true contact.

### Consequence
Stop tuning on broadcast footage. The unblocker is spec-compliant footage
(>=60fps, tripod, behind-baseline, rally-only) with hand-confirmed contact
frames. The detector needs a non-stroke filter (domain-standards §2) and a real
player-selection stage before its counts mean anything.

## 2026-10-04 (later): five stills from the target camera setup

Source: five still frames the owner extracted from an Insta360 X5 clip
(2576x1449, low tripod, centred behind the baseline, one target player, other
people on distant courts). Images are NOT in the repo (identifiable people).

| Test | Result |
|---|---|
| Full-frame MediaPipe, lite model | player found in **1 of 5** stills |
| Full-frame MediaPipe, heavy model | player found in **1 of 5** stills |
| Crop around the player (300 / 450 / 700 px), lite and heavy | player found in **5 of 5** stills at every crop size |

- The player is only about 80x200 px in a 2576x1449 frame, which MediaPipe's
  internal person detector misses. Cropping fixes it.
- The crop location was picked by eye, i.e. a perfect-detector oracle. This is
  an upper bound for the "detect player -> crop -> pose" stage in
  `docs/technical-plan.md` §3, not evidence a real detector will localize the
  player this well.
- Measured only "was a pose returned". Landmark quality (wrist position
  accuracy, which drives swing-speed peaks) was not evaluated.
- Five stills cannot train or validate anything: no time dimension, no labels.
  They answer detection-coverage questions only.

Consequence: M1's current full-frame approach fails on this camera setup. A
player detection + crop stage is a prerequisite, not an optimization.

## 2026-10-07: instructional forehand clip (480x854 portrait, 27.9fps, 8.7s)

An edited social-media short of one player (copyrighted, local only, not in
repo): at least three forehand swings, a hard cut near frame ~100, frozen /
slowed segments (frames ~121-154 and ~176-209 are near-identical stills) and
text overlays. Fails the fps and camera-stability gates, as expected.

- `pose_detection_rate` = **1.0** (242/242 frames). The player is roughly a
  third of the frame height here, versus about 14% in the Insta360 stills
  (1 of 5 found). Consistent with player size, not the model, being the limit.
- `detect_shots` returned 8 events: frames 26, 50, 60, 81, 103, 156, 170, 217.
- Reviewer's read (assistant, from a contact sheet at 11-frame spacing, NOT
  frame-labeled): about 3 real strokes, near 81, 170 and 217. The other 5 look
  like preparatory arm motion, non-racket-hand motion (3 of 8 events were
  attributed to the left wrist), and the edit cut near 103. Treat as an
  unverified estimate; the owner has not reviewed these frames.
- Takeaway: with a large player the pose side works; the over-firing remains.
  `combined_wrist_speed` takes the faster of both wrists every frame, so the
  non-racket hand (which swings hard in a forehand) can create peaks. A
  racket-hand-only signal is the obvious next experiment; untested here.

## 2026-10-07 (later): four side-view clips, 54 candidate events, assistant-labeled

Footage (all local, copyrighted social-media edits, not in repo): four
portrait side-view practice clips, 30fps, 9-34s. All fail the 60fps gate.
`pose_detection_rate` was 0.996-1.0 on all four (player is large in frame).

### Labels: PROVISIONAL, made by the assistant, not by a human reviewer
Each of the 54 candidate events (union of two detector modes, near-duplicates
merged) was judged from a 7-frame strip (-9..+9 frames) cropped to the player.
Labels: Y = a stroke is happening within +/-9 frames, N = no stroke (ready
stance, fidget), D = duplicate event for a stroke already counted, ? = unsure
(5 events, excluded from precision). Judged by eye at small size; the owner has
NOT verified them. Contact-frame accuracy was not assessed. Recall was not
assessed: only detector candidates were reviewed, so strokes the detector
missed are unknown.

### Results (49 judged events)
| Detector mode | events | Y | D | N | precision |
|---|---|---|---|---|---|
| default (faster of both wrists) | 49 | 23 | 5 | 17 | 0.51 |
| `hand="auto"` (inferred racket hand only) | 44 | 21 | 3 | 15 | 0.54 |

- Following one hand removed some false events but also lost 4 labeled strokes
  (fed1 frames 570 and 651 look like low shots where the other hand leads).
  Difference between modes is within noise at this sample size. Not adopted as
  a default.
- `infer_racket_hand` guessed "left" on IMG_3173 (a right-handed serve filmed
  from behind): 2D pixel peak speed is unreliable for this.
- One stroke often yields several events (5 duplicates): the 0.35 s minimum
  separation is too short for a swing plus its follow-through.

### What separates strokes from false events? (screened 5 features, AUC)
| Feature | AUC |
|---|---|
| peak wrist speed per torso length | 0.53 (chance) |
| wrist path length in +/-0.4 s | 0.67 |
| burst width | 0.57 |
| shoulder-width change (rotation proxy) | 0.77 |
| wrist height above hips | 0.84 |
| **wrist excursion in +/-0.4 s (max wrist-to-wrist distance / torso)** | **0.91** |

Speed alone does not work: false events (ready-stance racket movement,
follow-through) are as fast as real swings. Wrist excursion is the best lead,
but treat it as a hypothesis: labels are the assistant's own and were made by
looking at how large the swing is, so the feature and the label are partly
circular; five features were screened on 49 events from four clips; one clip
contributes a single labeled stroke. No filter has been implemented. Next step
is the owner re-checking the labels independently, then testing on held-out
footage.
