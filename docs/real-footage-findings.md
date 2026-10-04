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
