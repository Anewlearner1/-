# Domain standards: shot contact & stroke classification

Author: tennis-domain-consultant. Scope: give `data-labeler` (M2, correcting
`detect_shots()` candidates) and `ml-engineer` (M2 false-positive filtering,
future M3 classifier) operational, apply-by-eye rules. This is a definitions
document, not a model spec — it does not change any code in `ml/`, `cv/`, or
`labeling/`.

Grounding: read `docs/technical-plan.md` §3/§6, `ml/README.md`, and
`ml/shot_timing.py` (`detect_shots()`, `ShotEvent`) /
`cv/pose_overlay.py` (`PlayerLandmarkSequence`) before relying on this doc.
Current signal available to the detector: per-frame pixel-space pose
landmarks (MediaPipe-style skeleton, both wrists plus torso/hip/shoulder
joints), no ball position, no racket position. Everything below is written
against that constraint. A `labeling/` directory already exists in this repo
(`data-labeler`'s tool) — I did not read into it beyond confirming it's
there; this doc does not assume or depend on its internals.

---

## 1. What frame counts as "contact"

**Ground truth definition (what we actually want):** the contact frame is
the video frame where the racket face and the ball are touching — the
instant of the impact, not the backswing, not the follow-through.

**What pose alone can determine:** pose landmarks see the arm, not the
racket or ball. There is no landmark for "racket head" or "ball," so pose
alone cannot see impact directly. What it *can* see is the wrist-speed
peak, which is a proxy: wrist speed is near its local maximum around
contact for most groundstrokes and serves, because the arm is still
accelerating through the ball and begins decelerating into the
follow-through right after.

**Known imprecision of the proxy, stated honestly:**
- The wrist-speed peak frame and the true contact frame are usually close
  (informal expectation: within a few frames at typical 60fps capture) but
  are not guaranteed to coincide. For a flat, fast-armed shot the peak can
  land 1-3 frames *after* true contact (deceleration lags impact slightly).
  For a heavily topspin/brushing shot the peak can land *before* contact,
  since the racket is still accelerating up through contact on some grips.
- This doc cannot give an exact frame-offset correction — that requires
  comparing wrist-speed-peak frames against real ball-tracking contact
  frames on labeled footage, which doesn't exist yet (see §4). Until then,
  treat the wrist-speed peak frame as "contact ± a few frames," not exact.

**Operational rule for data-labeler right now:**
1. Start from the tool's candidate peak frame.
2. Step frame-by-frame around it. If the ball is visible in the source
   video at that resolution, use the frame where racket and ball visually
   overlap as the true contact frame — this overrides the pose peak
   whenever the ball is visible, since it's strictly more accurate.
3. If the ball is not visible/resolvable (common on phone footage, small
   fast-moving ball), fall back to the wrist-speed peak frame itself as the
   contact frame, and flag the label as proxy-based (not ball-verified) if
   the labeling tool has a field for that — this lets future accuracy
   analysis separate "ball-verified" from "pose-proxy" labels rather than
   silently mixing precision levels.
4. Do not hand-adjust the peak frame by a fixed offset (e.g. "always +2
   frames") — there is no evidence yet that a constant offset is correct
   across shot types; see §4.

## 2. Real stroke vs. non-stroke fast motion

`detect_shots()` fires on any sufficiently fast, sufficiently isolated
wrist-speed peak. Three known false-positive sources named in
`ml/README.md`: serve ball-toss, split-step recovery, emphatic non-stroke
gesture. Below are criteria `data-labeler` can apply by eye per candidate
frame, roughly in order of how decisive they are (check them in order;
stop at the first one that clearly resolves it).

**a. Did a backswing precede the peak?**
A real stroke has a visible backswing: the hand/racket-side arm moves
*backward and/or upward away from the ball-strike zone* for several frames
immediately before the fast forward motion that produces the peak. If the
arm was already near the body or moving forward the whole time with no
preceding backward excursion, it is very unlikely to be a stroke.
- Serve ball-toss: the *tossing* arm (usually the non-dominant one, often
  the slower of the two wrists the detector compares) moves upward with no
  backswing — it's a lift, not a swing. But the toss can make the *racket*
  arm look like it's mid-backswing at the same time, which is a real
  precursor to the actual serve stroke a few frames later; don't reject the
  later real peak just because a toss happened nearby.
- Split-step: both feet leave the ground roughly together, arms move only
  to help balance (short, symmetric, low-amplitude relative to a swing),
  no backswing shape at all.

**b. Body/shoulder orientation at the peak.**
A real stroke has the torso/shoulder line rotating so the body turns to
face (forehand) or turns away from (two-handed backhand) or turns sideways
to (one-handed backhand) the ball's approach direction, with hip/shoulder
rotation visible across the frames leading into the peak. A split-step or
a gesture keeps the torso roughly facing the net/opponent the whole time,
with no rotation building up.

**c. Hand trajectory shape.**
A real stroke's wrist path, plotted over the surrounding ~15-20 frames, is
a smooth, mostly single-plane arc: back → down/up (racket-drop, for
groundstrokes) → forward through contact → follow-through, i.e. one
continuous sweep with a single dominant speed peak. Non-stroke fast motion
tends to look different in trajectory shape:
- A gesture (e.g. a raised-fist celebration, wiping sweat, adjusting
  strings) is typically short, small-amplitude relative to a full swing,
  and not aimed toward where the ball actually is/was.
- A split-step's arm motion is brief and roughly symmetric left-right, not
  a single directed forward sweep.
- A toss is a near-vertical straight-line lift, not an arcing sweep.

**d. Timing relative to the rally.**
If frame-level rally/ball context is visible to the labeler (ball in frame,
opponent mid-swing, etc.), sanity-check: does a stroke here make sense in
the rally sequence (i.e., is the ball actually arriving at the player
around this frame)? A peak with no ball anywhere near the player at the
time is suspect regardless of how "swing-shaped" the arm motion looks.

**Labeler shortcut:** if (a) backswing present AND (b) torso
rotating/rotated toward the shot AND (c) trajectory is a single directed
arc — accept as a real contact. If none of the three hold, reject as noise.
If only one or two hold, it's a genuine edge case — label it but flag it
(e.g. "uncertain") rather than guessing, per §4.

## 3. Forehand / backhand boundary cases (for future M3)

Majority-case rule (not the hard part, stated for completeness): the
racket-side hand crosses the body's centerline before the swing, and
contact happens on the side of the body matching which hand is on the
grip and which direction the torso is rotating — forehand if the player's
dominant/racket hand swings from the same-side hip forward across the body
on the side the hand naturally swings without crossing the chest at
address; backhand if the swing crosses in front of the chest from the
non-dominant side. In practice: **forehand = torso rotates open toward the
ball with the hitting arm leading on the racket-hand side of the body at
contact; backhand = torso stays more closed/sideways and the hitting
motion originates from across the body.**

Boundary-case rules:

- **One-handed vs. two-handed backhand:** both are backhands for the
  FH/BH label — the two-hander/one-hander distinction is a separate,
  finer-grained attribute, not a different top-level class. Do not let
  "two hands on the racket" confuse a labeler into thinking it might be
  something else; it's still backhand. If M3 or the dataset schema ever
  wants the one/two-hand distinction as its own field, that's an additive
  attribute, not a change to this rule.
- **Two-handed backhand wrist selection:** since `detect_shots()` already
  picks "whichever wrist moved faster," a two-handed backhand can make
  *either* wrist register as the faster one depending on the player. Don't
  use "which wrist had the peak" as a signal for forehand/backhand at
  all — it is not reliable for that purpose. Use torso orientation (the
  rule above) instead.
- **Very open or very closed stance:** stance (the angle of the feet/hips
  relative to the net) is not the same thing as swing direction and must
  not be used as the primary signal. A player can hit an open-stance
  forehand or a closed-stance forehand; the same is true for backhand.
  Classify by which side of the body the swing originates from and which
  way the torso is rotating through contact, not by foot/hip stance angle.
  Stance angle is a useful secondary cue only when the swing-direction
  signal itself is ambiguous (e.g. a very abbreviated swing on a fast
  exchange) — in that case, the hitting-side foot is usually still planted
  on the same side as the true shot side, so it can break a tie, but it
  should not override a clear swing-direction read.
- **Slice hit unusually / unconventional grip or swing path:** classify by
  which side of the body the racket approaches the ball from and which
  direction the torso is rotating at contact — *not* by the shape or speed
  of the swing, spin, or how "clean" the stroke looks. A low, flat, or
  awkward slice is still a forehand or backhand by the same side/rotation
  rule; do not create a third "slice" bucket for FH/BH purposes. (If M3's
  eventual label schema wants shot-type/spin as a separate attribute
  alongside FH/BH, that's additive, same as the one/two-hander point
  above.)
- **Serve and overhead:** neither is a forehand or backhand in the
  conventional sense — both are hit from essentially the same overhead
  motion regardless of forehand/backhand "sidedness." If M3's scope
  includes serves, they need their own third class (or an explicit
  "N/A — serve/overhead" label); do not force them into FH/BH. This repo's
  current scope (per `docs/technical-plan.md` F3) does not say serves are
  excluded, so flag this ambiguity to `product-manager`/`ml-engineer`
  rather than assuming.
- **Ambiguous mid-transition frames:** if the contact frame lands during a
  clear directional transition (e.g., a player recovering awkwardly and
  slapping at a ball with poor form), classify by where the torso was
  rotating *at the contact frame itself*, not by the overall point of the
  rally or what shot "should" have been hit there.

## 4. What this document does NOT resolve

Being explicit rather than implying false precision:

- **No validated frame-offset between wrist-speed peak and true contact.**
  §1 gives a rule (prefer ball-visible contact, fall back to pose peak) but
  not a number. This needs a batch of real clips where both the pose peak
  frame and the ball-verified contact frame are labeled, so the actual
  offset distribution (mean, spread, whether it differs by shot type) can
  be measured. Until that data exists, nobody should claim a specific
  frame-count accuracy for M2.
- **The stroke-vs-noise criteria in §2 are heuristics, not a validated
  classifier.** They should make a human labeler faster and more
  consistent, but I have not tested them against a labeled set of real
  false positives (actual split-steps, actual tosses, actual gestures from
  real match footage) to confirm they actually separate the classes
  cleanly, or to find the edge cases they miss. Expect data-labeler to
  surface new edge cases once they apply these rules to real clips — this
  doc should be revised from that feedback, not treated as final.
- **The FH/BH boundary rules in §3 are unvalidated against real footage,**
  especially the "very open/closed stance" and "unusual slice" cases,
  which were reasoned from general tennis knowledge, not from reviewing
  actual clips in this dataset. A set of real labeled boundary-case clips
  (two-handed backhands from different players, deliberately awkward
  slices, extreme open-stance forehands) is needed before M3 training to
  confirm these rules produce consistent human-labeler agreement.
- **Serve/overhead classification scope is an open question for
  product-manager,** not something this doc can settle alone — see the
  serve bullet in §3.
- **This document does not cover ball-speed or bounce-point correctness**
  (M4/M5 scope) — out of scope for the current definitional gaps.

Bottom line: treat every rule above as the best currently-reasoned
starting point, not a settled spec. The real settling step is reviewing
labeler disagreement and false-positive/false-negative patterns on actual
labeled clips once `data-labeler`'s tool has produced some, and revising
this document from that evidence.
