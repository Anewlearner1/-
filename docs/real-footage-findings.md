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

## 2026-10-07 (latest): first precision/recall against owner labels (one clip)

Clip: Fonseca practice, 9.3 s, 30fps, pose rate 1.0. Owner-listed contacts
26, 95, 164, 237 (treated as the COMPLETE list: the owner added 237 in reply to
a request to list all contacts; not stated outright, see the label's meta).
One-to-one matching (a duplicate detection of one stroke counts as a false
positive), 4 true strokes:

| Mode | tolerance | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|---|
| default (both wrists) | +/-5 and +/-10 | 4 | 6 | 0 | **0.40** | **1.00** |
| `hand="auto"` | +/-10 | 4 | 5 | 0 | 0.44 | 1.00 |
| `hand="auto"` | +/-5 | 2 | 7 | 2 | 0.22 | 0.50 |

- Default mode found all 4 strokes. Its 6 false positives: 5 ready-stance
  movements (frames 43, 61, 73, 108, 125) and 1 duplicate of the 164 stroke (171).
- `hand="auto"` fires 5-7 frames later than the default (the other hand's
  wrist peaks earlier), so it only matches at the wider tolerance. No benefit.
- Wrist excursion (max wrist-to-wrist distance in +/-0.4 s, per torso length)
  ranked all 4 true strokes above all 6 false ones (AUC 1.00), but the margin
  is thin (lowest true 1.90, highest false 1.81), it is one clip with n=4 vs 6,
  and a duplicate is better removed by merging nearby events than by a
  threshold. In-sample lead only; no filter implemented. Needs a second clip
  with a complete owner label to test on held-out data.

## 2026-10-07 (erratum): "swing" is not "shot"

For Fed 1 the owner reported seeing no ball at frames 570, 589 and 651. Those
are stored as `not_contacts` in the label's meta (cause not stated: practice
swings, or a ball too small to see at 360x640). The assistant had labeled all
three as real strokes from arm motion alone.

- A pose-only detector counts swings, and a swing without a ball looks the same
  as a hit. Telling them apart needs a ball signal (planned ball tracking, M4/M5)
  or footage where the ball is reliably visible. This is the plan's own reason
  for fusing ball trajectory with swing speed.
- Reclassifying those three as not-a-shot, the assistant's provisional
  precision drops from 0.51 to 0.44 (default mode) and 0.54 to 0.51 (`hand="auto"`).
  Still provisional and still the assistant's labels, except for those three.
- Detections that landed on owner-rejected frames: default mode 3 of 9 on Fed 1
  (570, 589, 651); `hand="auto"` 1 of 7 (589). Reported by
  `ml.eval_shot_timing` as `known_false_positives`, a lower bound on false
  positives while the contact list is partial.

## 2026-10-07 (held-out test): wrist excursion does not transfer between clips

Fed 1 label completed by the owner: contacts 132, 183, 204 have a ball; 445, 570,
589, 651 do not; detections at 62 and 162 not mentioned and assumed not contacts
(complete-list inference, see the label's meta).

| Mode | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| default | 3 | 6 | 0 | 0.33 | 1.00 |
| `hand="auto"` | 3 | 4 | 0 | 0.43 | 1.00 |

Held-out check of the wrist-excursion idea (threshold chosen on Fons, applied
to Fed 1):
- Rank order still separates (AUC 1.00, 3 true vs 6 false), but only barely:
  lowest true contact 2.47, highest false event 2.46.
- The Fons-derived threshold of 1.85 keeps 3/3 true contacts AND 5/6 false
  events. Absolute values differ between clips (Fons: false <= 1.81, true >=
  1.90; Fed 1: false <= 2.46, true >= 2.47), so there is no transferable cutoff.
- The false events with large excursion are mostly the owner's no-ball swings
  (589: 2.46, 445: 2.41, 570: 2.08), which are real full swings.

Conclusion: a pose-only filter cannot separate shots from practice swings, and
wrist excursion is not a usable fixed filter. Not implemented. What would
separate them is whether a ball is at the racket near the swing, i.e. a ball
signal. Two clips with complete owner labels (7 true contacts, 15 non-contact
detections) now exist as a small test set for that.

## 2026-10-07 (ball-colour experiment, and a correction)

Question: can "is there a ball-coloured blob near a wrist within +/-8 frames of
the event" separate ball contacts from non-contacts? Method fixed before the
first run, run once, not tuned (`ml/ball_experiment.py`): HSV hue 25-50,
saturation >= 90, value >= 150; blob area 3-200 px; within 2 torso lengths of a
wrist. Test set: the 19 default-mode detections on Fons (4 true, 6 false) and
Fed 1 (3 true, 6 false) against the owner's labels.

| Result | Value |
|---|---|
| AUC (frames-with-blob, true vs false) | 0.85 |
| Rule ">= 1 frame with a blob": true kept | 6 of 7 |
| same rule: false kept | 2 of 12 |
| Before the rule: precision / recall | 0.37 / 1.00 (7 of 19 detections true) |
| After the rule | precision 0.75, recall 0.86 (6 TP, 2 FP, 1 FN) |

The rule threshold (>= 1 of {1,2,3} tried) was picked after seeing results.
Small sample: 7 true events, 2 clips, one court colour each (blue indoor, green
outdoor). Colour cue only: a ball-coloured racket, shirt or shoe would also
fire, and lighting or compression could hide a ball that is there. Not tested
on the behind-the-baseline phone footage.

### CORRECTION to the "swing is not shot" erratum above
The two false positives that fired the rule (Fed 1 frames 570 and 651) are NOT
a racket or shirt: crops show a clear ball, at 577-578 (touching the racket tip)
and 643-644 (in flight). The owner reported no ball at the detected frames
570/589/651 themselves; the ball is visible 7-8 frames away. So those events may
be real shots whose contact is offset from the detected frame, and the earlier
claim that they were practice swings a pose-only detector could not tell from
hits is NOT established. Their `not_contacts` entries are now marked
`under_review` in the label meta; `known_false_positives` for 570 and 651 are
unconfirmed, and so is the 0.44 / 0.51 provisional-precision update. Frame 589
and 445 had no blob in the window (still consistent with no ball).
If 570 and 651 are real shots, the rule above keeps 8 of 9 true events and 0
false ones; this has not been confirmed.

### RESOLVED (owner re-check): 570 and 651 are NOT contacts
The owner watched frames 570-580 and 640-651 and confirmed the racket does not
hit the ball in either window. The correction above (that they may be real shots
offset by 7-8 frames) is withdrawn; the original labels stand and
`under_review` is removed from the label meta. The 0.44 / 0.51 provisional
precision figures are valid again (still the assistant's labels apart from the
owner-confirmed frames).

What this changes about the ball-colour result: the two "false positives" of
the rule are real false positives, and they are a ball present near the racket
WITHOUT being struck. So a ball-coloured blob near a wrist is a necessary-looking
but not sufficient cue: precision 0.75 / recall 0.86 stand as measured, and the
remaining errors are the case a ball-trajectory cue (direction or speed change
at the racket) exists to handle. What the ball was doing at 577-578 and
643-644 is unknown.

## 2026-10-07 (ball-trajectory experiment): result conflicts with the labels, PENDING

Cue: from a side-on camera a struck ball reverses horizontal direction near the
racket. Fixed before a single run (`horizontal_reversal` in
`ml/ball_experiment.py`): +/-12 frames, ball-coloured blob nearest a wrist
within 4 torso lengths, reversal = a split with >= 2 points each side whose
median horizontal speeds have opposite signs and are each >= 2 px/frame.

| Owner label | reversal yes | reversal no | too few ball points |
|---|---|---|---|
| contact (7) | 4 | 0 | 3 |
| not a contact (12) | 3 | 0 | 9 |

The cue never said "no", so as measured it does not separate the labels. The
three "not a contact" events with a reversal are Fed 1 570 and 589 (same ball,
frames 575-580) and 651 (frames 641-646). Their tracks match confirmed hits:
ball approaching at about 35-40 px/frame, then leaving the other way (570:
229, 200, 171, 155 then 220, 288; confirmed hit 129: 335 ... 184 then 225).
In the assistant's decoded frames the racket appears to meet the ball at 578
and 645 (low backhand).

This conflicts with the owner's re-check ("no contact in 570-580 and
640-651"). Possible reasons, not resolved: the owner and the assistant are not
looking at the same frames (variable-frame-rate mp4s can number frames
differently across tools; this would also bear on the consistent "detector 3
frames early" offset), the ball rebounded off something other than the racket,
or the assistant is misreading the frames. The decoded frames were sent to the
owner to judge directly. No conclusion until then; the labels are unchanged.

## 2026-10-07 RESOLUTION: the conflict was frame numbering. Current state.

The owner judged the assistant's decoded frames (numbers burned in) and
confirmed contact at decoder frames 578 and 645. The earlier "no contact in
570-580 / 640-651" answers were given using the owner's video player, which
numbers frames about 3 higher than OpenCV's decoder for this clip (likely a
variable-frame-rate mp4). Fed 1 is now labeled in decoder numbering: contacts
129, 180, 200, 578, 645 (180 is an estimate; see the label meta).
Fons and Fed 2 labels are still in the owner's player numbering.

### Retracted (superseded by this section)
- "Detector fires about 3 frames early": most likely the player/decoder offset,
  not detector behaviour. On decoder frames, detections at 129 and 204 sit at
  or just after the ball-on-racket frames (129-130, 200-201).
- "570/589/651 are practice swings; a pose-only detector cannot tell swings
  from hits" and the "ball near the racket without being struck" reading: both
  came from the numbering mismatch. 570 and 651 are hits; 589 is the
  follow-through duplicate of the 578 hit.
- The 0.44 / 0.51 provisional-precision update that reclassified those frames.
- The wrist-excursion held-out numbers used the wrong Fed 1 labels. With the
  corrected labels the lowest true contact has excursion 1.53, below most false
  events, so the idea is even weaker; it stays dropped.

### Current results (2 clips, complete owner labels, +/-10 frames, one-to-one)
| Method | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| pose only (default) | 9 | 10 | 0 | 0.47 | 1.00 |
| pose + ball-coloured blob near a wrist in +/-8 frames | 8 | 0 | 1 | 1.00 | 0.89 |

- The one miss (Fed 1 frame 180) had no visible ball blob in the window.
- The ball-trajectory reversal cue adds nothing on top: it never returned
  "no" (6 reversals on true hits, 1 on the 589 duplicate, 12 undecidable).
- Caveats: 9 true and 10 false events, 2 clips, both practice footage where a
  ball is near the player essentially only when they hit it. In rallies or
  multi-ball drills a ball can pass near a player who does not hit it; that
  case is untested. The ">= 1 frame" rule was chosen after seeing earlier
  results. Not tested on behind-the-baseline phone footage.

### Process lesson
Labels must be given on the decoder's frames, not a media player's frame
counter. `labeling/label_shots.py` already burns decoder frame numbers into
its stills; use it (or burned-in contact sheets) for all future labels.

## 2026-10-07: first held-out test of the ball-colour rule (Fed 2), PENDING label review

Fed 2 (34 s, 23 default-mode detections) was not used to choose anything. The
owner judged all 23 detections on decoder strips (7 tiles at every 3rd frame):
hits at E5, E6, E9, E19, E21, E22; none at the other 17 (E3 "really none").
Rule unchanged (>= 1 ball-blob frame in +/-8).

| Method | detections kept | hits kept | precision |
|---|---|---|---|
| pose only | 23 | 6 of 6 | 0.26 |
| + ball-colour rule | 11 | 5 of 6 | 0.45 |

Far worse than the 1.00 on the two clips used to build the rule. Of the 6
retained "no hit" events, one is a 3-pixel speck on a hand (E18, a genuine
false detection). The other four (E1, E3, E13, E16) contain a real ball; on an
every-frame view the ball meets the racket at f52, f128, f504 and f576, frames
the every-3rd-frame strips skipped or showed poorly. Either those are hits the
strip design hid from the owner, or the ball passed the racket without being
struck. Sent to the owner as every-frame strips; the result above stands until
then. Lesson regardless: review strips must show every frame near contact.
Recall is unknown for Fed 2 (missed hits outside the 23 strips not reported).
