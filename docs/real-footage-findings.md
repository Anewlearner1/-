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

### Update: owner re-judged the 4 disputed Fed 2 events on every-frame strips
E1 f52, E3 f128, E13 f504, E16 f576 are all hits. Their earlier "no hit" came
from the every-3rd-frame strips skipping the contact frame. Fed 2 now has 10
known hits. Held-out result (rule unchanged, one-to-one, +/-10 frames):

| Method | TP | FP | FN | precision | hits kept |
|---|---|---|---|---|---|
| pose only | 10 | 13 | 0 | 0.43 | 10 / 10 |
| + ball-colour rule | 9 | 2 | 1 | 0.82 | 9 / 10 |

Remaining rule errors: 511 (follow-through of the 504 hit, a duplicate), 646 (a
3-pixel speck on a hand), and the miss at 731 (a hit whose ball the colour
filter did not pick up). Still open: the other 13 "no hit" detections were
judged on every-3rd-frame strips only; E11 (418), E18 (646) and E23 (952) were
sent back as every-frame strips. Recall for Fed 2 is unknown (hits outside the
23 detections not reported).

### Update: E11, E18, E23 are hits too (owner, every-frame strips: 419, 648, 954)
Fed 2 now has 13 known hits. Current results, one-to-one, +/-10 frames,
"recall" = share of the owner's known hits (hits no detection covered were
never reported, so true recall may be lower):

| Clip | Method | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|---|
| Fons | pose only | 4 | 6 | 0 | 0.40 | 1.00 |
| Fons | + ball rule | 4 | 0 | 0 | 1.00 | 1.00 |
| Fed 1 | pose only | 5 | 4 | 0 | 0.56 | 1.00 |
| Fed 1 | + ball rule | 4 | 0 | 1 | 1.00 | 0.80 |
| **Fed 2 (held out)** | pose only | 13 | 10 | 0 | 0.57 | 1.00 |
| **Fed 2 (held out)** | + ball rule | 10 | 1 | 3 | **0.91** | **0.77** |
| All 3 | pose only | 22 | 20 | 0 | 0.52 | 1.00 |
| All 3 | + ball rule | 18 | 1 | 4 | 0.95 | 0.82 |

Reading it:
- Pose alone finds every known hit but about half its detections are not hits
  (ready-stance movement, cuts, follow-through duplicates).
- The ball-colour rule removes almost all of those (20 -> 1 false), at the cost
  of about 1 hit in 5: all 4 misses (Fed 1 180; Fed 2 419, 731, 954) are hits
  where the colour filter found no ball blob near a wrist. The one remaining
  false positive is a follow-through duplicate (Fed 2 511 after the 504 hit).
- Every number is from three short, side-view, 30 fps social-media practice
  clips with a large player and a clearly coloured ball. Not representative of
  the target camera setup (behind the baseline, small player) or of rallies
  where balls pass players who do not hit them.
- Label caveat: 10 of 42 detections (all on Fed 2) were judged "no hit" only on
  every-3rd-frame strips; Fons and Fed 1 labels came from frame lists. Sparse
  strips had hidden 7 Fed 2 hits, so a few more may still be hidden.

## 2026-10-07: ball filter and merge shipped as options (off by default)

`ml/ball_filter.py` (the ball-colour rule, unchanged) and
`detect_shots(merge_within_s=...)`. Re-running all three labeled clips through
the shipped code reproduced the experiment numbers exactly.

| Setting | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| pose only | 22 | 20 | 0 | 0.52 | 1.00 |
| merge 0.5 s | 22 | 15 | 0 | 0.59 | 1.00 |
| ball filter | 18 | 1 | 4 | 0.95 | 0.82 |
| ball filter + merge 0.5 s | 18 | 0 | 4 | 1.00 | 0.82 |

- Merge at 0.5 s removed 3 of the 4 follow-through duplicates (Fons 171, Fed 2
  430 and 511) and two other non-hits (Fons 73, Fed 2 312) without removing any
  hit. It cannot catch Fed 1 589 (19 frames after its hit) without also
  risking genuine hits 20 frames apart.
- The window was chosen with these clips in view, and the ball rule was built
  on two of them, so the combined 1.00 / 0.82 is optimistic. Fed 2 alone, held
  out for the ball rule only: 1.00 / 0.77 with both options.

## 2026-10-07: two more clips (Komura forehand, "Which Forehand" montage) — no labels yet

| Clip | Format | Player in frame | Pose rate | Pose-only detections | Gate |
|---|---|---|---|---|---|
| Komura | 720x1280, 30 fps, 16.9 s, fixed camera behind the baseline, one player | 28% of frame height (363 px) | 1.00 | 9 (frames 38, 103, 175, 250, 330, 396, 415, 471, 481) | fps FAIL, stability PASS |
| "Which Forehand" | 720x1280, 30 fps, 27 s, edited montage of several named players | 46% | 0.945 | 14 | fps FAIL, stability FAIL |

- The montage has cuts, captions and different players and courts: not a
  usable evaluation clip. Its detections are not analysed.
- Komura is the closest clip so far to the target setup (fixed, behind the
  baseline, single player) but still 30 fps and the player is larger than in the
  Insta360 stills (28% vs about 14% of frame height). Full-frame pose worked at
  28% (and on Fed 1 at 20%), consistent with player size being the limit.
- The ball-colour filter is uninformative on Komura: 11-17 of 17 frames "see a
  ball" for every detection, because the player's white shirt carries a
  yellow-green graphic that matches the colour range. It keeps all 9 detections.
  A concrete case of the colour cue's known fragility (clothing).
- Merge at 0.5 s removes the 481 detection (10 frames after 471).
- Every-frame strips for the 9 detections sent to the owner; nothing is
  labeled, so there are no precision/recall figures for these clips.

### Komura owner review (strip level, hit frames NOT yet located)
Owner: K1-K6, K8, K9 contain a hit; K7 (frame 415) contains none (assistant's
reading of the sentence "only K7 has no hit"; confirm if wrong). Strips are
+/-9 frames around each detection, so the windows of K8 (471) and K9 (481)
overlap and one hit could be what makes both strips "have a hit"; K6 (396) and
K7 (415) also overlap. No hit frame was named, so no label file was written and
no one-to-one precision/recall is computed.

Strip-level reading only: pose only 8 of 9 detections have a hit in their strip
(0.89); the ball-colour filter keeps all 9 (fooled by the shirt graphic);
`merge_within_s=0.5` drops K9 (481), which is wrong if K9 is a separate hit and
right if it is the follow-through of K8's. Next: the owner names the hit frame
in each strip (frame numbers are printed under every tile), which gives located
labels and settles the K8/K9 question.

### Komura located hits (owner, every-frame decoder strips) and first timing spread
Hits: K1 30, K2 111, K4 257, K5 329, K6 404, K8 478 (K9 479 recorded as the same
stroke as K8, one frame apart). K3 has a hit but no frame was given; it is left
out of the scoring below. K7 (415) has no hit and sits 11 frames after the K6
hit: a follow-through duplicate. Label is partial (hits outside the nine
detections were not reported), file `labeling/labels/8ede2a37-...json`.

Timing of the pose-speed peak against the located hit (detected - hit):
+8, -8, -7, +1, -8, -7 frames (about +/-0.27 s at 30 fps), mixed signs. So the
peak is not a precise contact locator: only 1 of 6 is within 5 frames, all 6
within 8. The earlier "3 frames early" pattern is not supported.

One-to-one scoring (8 detections after leaving out K3's 175; 6 located hits):

| Setting | tol +/-5 | tol +/-8 and +/-10 |
|---|---|---|
| pose only | P 0.25 / R 0.33 | **P 0.75 / R 1.00** (FP: 415, 481) |
| merge 0.5 s | P 0.14 / R 0.17 | **P 0.86 / R 1.00** (FP: 415) |
| ball filter (alone or with merge) | same as without it | same: no detection removed |

- Merge removed 481, a true duplicate of the 478 hit, but did not remove 415
  (19 frames after 404, outside the 15-frame window). A window long enough to
  catch it (0.65 s) would also merge the real Fed 1 hits that were 20 frames apart.
- The ball filter did nothing here: the shirt graphic is ball-coloured.
- Consequence for the M2 criterion: ADR 0002 says +/-10 frames. At 30 fps that
  is 0.33 s and is needed, since the peak lands up to 8 frames from the hit; a
  tolerance in seconds (0.33 s) would stay comparable at 60 fps. Not changed
  here; raise with the owner.

### Komura K3 corrected: a ball is visible but there is no hit (owner)
K3 (detection 175) is a non-contact. It was first reported as having a hit, then
corrected: a ball is in view but the racket does not strike it. Komura's nine
detections now all have an owner verdict: 6 hits (K1, K2, K4, K5, K6, K8), and 3
non-contacts (K3 175, K7 415, K9 481, the last a duplicate of the K8 hit).
`not_contacts` in the label meta is [175, 415].

Komura alone (tolerance 0.33 s = 10 frames): pose only 6 TP / 3 FP (P 0.67, R 1.00);
merge 0.5 s 6 / 2 (P 0.75); the ball filter removes nothing (shirt graphic).

All four clips, 0.33 s tolerance, TP/FP/FN (K3 is a ball-in-view-but-not-struck
case, the scenario flagged as untested; whether the ball filter would catch it is
unknown because the shirt graphic hides it):

| Setting | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| pose only | 28 | 23 | 0 | 0.55 | 1.00 |
| merge 0.5 s | 28 | 17 | 0 | 0.62 | 1.00 |
| ball filter | 24 | 4 | 4 | 0.86 | 0.86 |
| ball filter + merge | 24 | 2 | 4 | 0.92 | 0.86 |

The earlier "1.00 / 0.82" for the three side-view clips fell to 0.92 / 0.86 once
Komura (a clip with clothing the colour rule mistakes for a ball) was added.
Against ADR 0002 (precision and recall both >= 0.90 on >= 10 unseen
spec-compliant clips) recall 0.86 is short, and none of these clips is
spec-compliant, so this is not an acceptance result.

## 2026-10-07: first forehand/backhand check against owner labels (M3)

Owner labeled the 28 confirmed hits from sheets M1-M28: **backhand = 3** (Fons
4th hit, Fed 1 4th and 5th), **forehand = 25**. Labels stored as `stroke_labels`
in each `labeling/labels/*.json` (aligned with `contact_frames`). Fons labels are
still in the owner's player numbering (about 3 frames off the decoder); the
classifier reads a window, so this should matter little.

`python -m ml.eval_stroke_classification` (oracle mode: classified at the
labeled contact frame), hand inferred, unknown counts as wrong:

| | forehand | backhand | other | unknown |
|---|---|---|---|---|
| truth forehand (25) | 2 | 7 | 0 | 16 |
| truth backhand (3) | 0 | 2 | 0 | 1 |

Accuracy **0.14** (4/28), coverage 0.39 (11/28 answered). ADR 0002 asks for >= 0.85
on located hits: **not met**, and these clips are not spec-compliant anyway.

Diagnosis (same hits, racket hand forced instead of inferred, nothing tuned):

| Clip | inferred hand | result as shipped | hand = right |
|---|---|---|---|
| Fons (4) | left | 4 unknown | 4 unknown |
| Fed 1 (5) | right | 4 right, 1 wrong | 4 right, 1 wrong |
| Fed 2 (13) | right | 13 unknown | 13 unknown |
| Komura (6) | **left** | **6 wrong** | **6 right** |

- **Hand inference is wrong on Komura** (behind view, all forehands): the peak-wrist-speed
  rule picked "left", which turns every answer around. With the hand forced to right
  it is 6/6. Hand inference has now failed on Komura and on IMG_3173 (behind views).
- **The classifier abstains on side-on clips** (17 of 28: all of Fons and Fed 2), by
  design when the 2D shoulders are edge-on. That is most of the labeled data.
- With the hand forced to right: 10 correct, 1 wrong, 17 unknown (10/11 = 0.91 when it
  answers, coverage 0.39, accuracy over all hits 0.36).
- **The labeled set cannot show it is useful**: 89% of hits are forehands, so
  answering "forehand" every time scores 25/28 = 0.89, above any of the figures above.
  Only 3 backhands exist; the one answered Fed 1 error may be one of them.
- The rule was written against synthetic footage; this is its first real test.

Consequence for M3: not met; the cheapest likely fix is to take the racket hand as an
input (asked at upload) instead of inferring it, plus a different cue for side-on
views. Not implemented. More backhand footage is needed before any accuracy figure
for backhands means anything.

## 2026-10-07: three backhand-focused clips, assistant's provisional classification (owner check pending)

Clips (all 720x1280, ~30 fps, fail the 60 fps gate; hand assumed RIGHT for both players, not confirmed): Sinner cross-court practice (B1-B17), Sinner backhand practice (B18-B22, cuts between views), Ruud backhand rally (B23-B31). 31 pose-only detections, every-2nd-frame strips of +/-8 frames. The classifier's predictions were deliberately not looked at before this labeling.

These are the ASSISTANT's reading of small strips, not ground truth; the owner has not yet checked them and no hit frame was located.

| Assistant label | Events |
|---|---|
| Backhand hit, confident | B2, B4, B7, B8, B11, B12, B13, B14, B16, B18, B24 |
| Backhand hit, probable | B20, B21, B22, B26, B28, B29, B31 |
| No hit | B3 (follow-through 12 frames after B2), B6 (takeback for the next hit), B9 (follow-through of B8), B15 and B19 (camera cuts), B17 and B23 (follow-through of a hit before the window) |
| Unsure | B1, B5, B10, B25 (looks like the early part of B26's stroke), B27 (camera pans away), B30 (13 frames before B31, probably one stroke) |

No forehands were seen in these clips.

Event frames: B1 sinner_cc f19, B2 sinner_cc f37, B3 sinner_cc f49, B4 sinner_cc f110, B5 sinner_cc f193, B6 sinner_cc f227, B7 sinner_cc f257, B8 sinner_cc f336, B9 sinner_cc f348, B10 sinner_cc f386, B11 sinner_cc f409, B12 sinner_cc f484, B13 sinner_cc f553, B14 sinner_cc f632, B15 sinner_cc f661, B16 sinner_cc f700, B17 sinner_cc f788, B18 sinner_bh f110, B19 sinner_bh f190, B20 sinner_bh f258, B21 sinner_bh f405, B22 sinner_bh f484, B23 ruud f24, B24 ruud f99, B25 ruud f163, B26 ruud f178, B27 ruud f328, B28 ruud f374, B29 ruud f395, B30 ruud f468, B31 ruud f481.

### Owner check of the backhand classification (2026-10-07)

Owner corrections: B25/B26 and B30/B31 are each **one** hit (B25 and B30 are duplicates, counted as no-hit); B20 and B21 are hits; B17, B23 and B27 as the assistant said. Unmentioned events accepted. Result: **18 backhand hits** (Sinner CC 9, Sinner BH 4, Ruud 5), 9 no-hit detections, 4 left unsure (B1, B5, B10, B27, excluded). Labels written to `labeling/labels/{2cd68ca3,b9f75af1,f76ed687}-*.json`, with partial lists. The contact frames are the **detector's event frames**, not owner-located hit frames. Racket hand is assumed right.

**M3, oracle mode** (classified at the labeled frame):

| | inferred hand | hand forced right |
|---|---|---|
| 18 new backhands | **2/18** (11 called forehand, 3 other, 2 unknown) | **10/18** (4 forehand, 4 other) |
| all 46 hits (25 FH, 21 BH) | 6/46 = 0.13 | 20/46 = 0.43 |

Per clip, hand forced right: Sinner CC 7/9 (2 other), Sinner BH 2/4, Ruud 1/5.

- **Hand inference picked "left" on all three new clips.** It has now been wrong on 5 behind or behind-ish clips: Komura, IMG_3173, Sinner ×2 and Ruud. Fed 1 and Fed 2 are the only clips it got right. A two-handed backhand moves both wrists, so "fastest wrist = racket hand" is not a usable rule.
- Even with the correct hand, backhand accuracy is 0.56. Answering "forehand" every time now scores 25/46 = 0.54 overall, so the classifier still does not beat the trivial baseline on the combined set (0.43 vs 0.54).
- M3 (≥0.85) is **not met**.

**M2 on these clips** (held out: neither the ball-colour rule nor the merge window was tuned on them). The hits were found only among detections, so recall is an upper bound:

| | kept | hits | no-hit | unsure | precision (reviewed) | recall (of 18) |
|---|---|---|---|---|---|---|
| pose only | 31 | 18 | 9 | 4 | 0.67 | 1.00 |
| merge 0.5 s | 27 | 16 | 7 | 4 | 0.70 | 0.89 |
| ball filter | 24 | 18 | 4 | 2 | 0.82 | 1.00 |
| ball + merge | 22 | 16 | 4 | 2 | 0.80 | 0.89 |

- The ball filter removed all 5 Sinner CC no-hits and lost no hit. It had no effect on Ruud (all 3 no-hits kept).
- **Merge hurts on Ruud**: in both duplicate pairs the earlier event is the takeback (163, 468) and the real hit is 13–15 frames later. "Keep the earlier event" drops the hit. That was the opposite case of the forehand clips the window was chosen on. Keeping the faster or later peak would be the candidate fix, but it has not been tested.

## 2026-10-07: racket hand as user input + wrist-gap cue (M3, experiment 1)

Following the research summary (no surveyed tool infers the racket hand reliably; the one OSS pose classifier asks for a `--left-handed` flag), two changes:

1. **Racket hand is a user input.** `POST /upload` takes an optional `racket_hand` (`left`/`right`). The worker labels FH/BH only with `--classify-strokes` **and** a given hand, and never infers it.
2. **Wrist gap**: the median distance between the two wrists over ±5 frames (±0.17 s) around contact, in torso lengths. A small gap means both hands are on the racket.
   - When the takeback rule would say "unknown", the gap answers with a fixed low confidence.
   - It also vetoes the serve gate: 4 two-handed backhands had been called "other".

Screening on the 46 hits (AUC, backhand gap < forehand gap): median ±5 frames **0.90**, median ±2 frames 0.77, minimum ±5 frames 0.57. Wrists overlap in 2D on many forehands, so the minimum is useless. Within the two mixed clips the gap separates the classes: Fed 1 backhands 0.20 and 0.45 vs forehands 0.71–0.91; Fons backhand 0.24 vs forehands 0.29–0.62.

Results (oracle, 46 hits: 25 forehands, 21 backhands, hand given as right):

| | before | after, threshold chosen on the same 46 (in-sample) | after, **leave one clip out** |
|---|---|---|---|
| accuracy | 0.43 | 0.78 | **0.61** (FH 13/25, BH 15/21) |

Always answering forehand scores 0.54. With the hand inferred instead, the result is 0.35 held out, so the user-input hand matters more than the new cue.

- **The threshold does not transfer across cameras.** The leave-one-clip-out cut ranged from 0.28 to 0.64 depending on which clip was held out. The shipped value, 0.30, is the in-sample cut. Per clip, held out: Fons 1/4, Sinner CC 9/9, Fed 2 5/13, Komura 6/6, Fed 1 4/5, Sinner BH 2/4, Ruud 1/5.
- Five of the seven clips contain only one stroke type, so a per-clip number partly measures where the threshold landed, not whether the classifier tells the classes apart.
- **M3 (≥ 0.85) is still not met.** Next candidates: YOLO racket detection for the racket side at contact, and MediaPipe world landmarks for the side-on clips.

Code review fixes (same day):
- The serve gate now runs **before** the edge-on check, so a side-on serve stays "other" and is not turned into a gap "forehand". That moved the in-sample figure from 0.80 to 0.78. The leave-one-clip-out figure is unchanged at 0.61.
- `shots.stroke_confidence` and the API field `fh_bh_confidence` now distinguish wrist-gap guesses (0.3) from takeback-rule labels.
- A classifier error no longer discards the detected shots.

## 2026-10-07: racket detection experiment (M3, experiment 2) and merge keep-policy (M2)

**Detector.** MediaPipe ObjectDetector with EfficientDet-Lite2 (Apache-2.0, free; Google storage). This replaced YOLOv8 because PyTorch's CPU index and Hugging Face are blocked by the sandbox proxy. Score ≥ 0.2, class `tennis racket`, every 2nd frame from −12 to +4 frames of each of the 46 hits, about 0.22 s per 720p frame on CPU. The racket was found in **70–97% of frames** per clip (Fed 2 lowest at 0.70, Fons highest at 0.97).

**Racket hand from racket proximity** (the wrist nearest the racket-box centre wins a vote per frame; all 7 players are right-handed):

| | right clips |
|---|---|
| current peak-speed rule | 2/7 |
| racket proximity, every frame | 5/7 (both Sinner backhand-only clips → left: in a two-hander the top hand is the left) |
| racket proximity, only frames with wrists ≥ 0.4–0.6 torso apart | 6/7 (Sinner CC still left) |

The gap cut-off was chosen while looking at these 7 clips, and none of them has a left-handed player. This is promising as the default when the user picks 不確定, but it is **not shipped**: it needs left-handed footage first.

**Racket side as an FH/BH cue: no gain.** I measured the racket-centre lateral offset before contact against the wrist offset over the same frames:

| cue | AUC, all 46 | AUC, 29 hits not edge-on |
|---|---|---|
| racket centre | 0.86 | 0.96 |
| wrist | 0.86 | 0.95 |

The racket adds nothing over the wrist. Swapping the takeback rule for the mean wrist lateral (−0.4 to −0.07 s) plus the gap fallback gives 0.63 leave-one-clip-out, against 0.61 now, which is within noise. The remaining errors are concentrated in:
- side-on clips: Fons and Fed 2, 6/17;
- Ruud: 0/5 in every variant. The sign is consistently flipped, so the shoulder left/right ordering is probably wrong for that camera angle.

**Merge keep-policy** (`detect_shots(merge_keep=...)`; evaluated by the ml-engineer role on all 7 clips at 0.33 s):
- "later" and "stronger" fix Ruud.
- But they lose hits on Sinner CC (they keep the non-contact at 49 over the hit at 37), on Fed 2 ("later" loses 419), and on Fons with the ball filter (they keep the follow-through 171, which the filter then drops).
- **The default stays "earlier"**; the option exists for future data.
- Pooled: pose only 0.69 / 1.00; earlier 0.72 / 0.96; stronger 0.74 / 0.98; with the ball filter, 0.87–0.91 precision and 0.85–0.91 recall.
- Fed 2's meta has no `not_contacts`, so its non-hits are "unreviewed" rather than counted as FP.

**PM decision (ADR 0005 authority):**
- Stop tuning rules on these 46 hits. Every change since the hand fix moves the result by one or two hits, which is noise at n = 46 with 5 of 7 clips containing a single stroke type.
- The next real gain needs **data**: clips with both forehands and backhands, a left-handed player, and the camera the product will support.
