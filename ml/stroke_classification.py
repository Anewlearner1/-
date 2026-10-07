"""M3: forehand / backhand classification of detected shots -- rule-based,
pose-only: 3D world landmarks when available (step 0), else 2D pixel space.

STATUS: 0.93 leave-one-clip-out on 46 owner-labeled real hits WITH world
landmarks and the hand given (7 non-spec clips, docs/real-footage-findings.md);
2D-only input stays at 0.61. ADR 0002's 0.85 is only "met" on spec-compliant,
unseen footage, which has not been tested. The
synthetic tests (tests/test_stroke_classification.py) check *logic* only.
Racket-hand inference failed on 5 of 7 real clips: pass ``hand`` whenever
the user has given it.
Every threshold below is a starting value reasoned from the sibling
tennis-form-coach project and docs/domain-standards.md, except
WRIST_GAP_BACKHAND and LATERAL_3D_FOREHAND_M, which were chosen on the 46
labeled hits. Quote only leave-one-clip-out figures, never in-sample ones.

Why a rule baseline: with zero labels a learned model cannot be trained or
validated; a transparent rule is debuggable and gives the future model a
baseline to beat. The interface (``classify_shot`` /
``classify_shots(..., classifier=...)`` returning ``StrokeClassification``)
is deliberately model-agnostic so a learned classifier can replace
``classify_shot`` unchanged for callers.

Rule (domain-standards.md section 3: "which side of the body the swing
originates from" -- NOT which wrist peaked, NOT stance angle, NOT swing
shape/slice)
---------------------------------------------------------------------------
1. Racket hand: given, or inferred as the wrist with the higher *peak*
   speed over the clip (as in the sibling's detect_handedness: peak speed,
   not path length). ``detect_shots``' per-event ``wrist`` field is
   deliberately NOT used -- domain-standards says a two-handed backhand can
   make either wrist the faster one.
2. Overhead/serve gate: racket-hand wrist well above the head (or far above
   the hips) at contact -> "other" (thresholds copied from the sibling's
   serve gate; the serve scope is an open product-manager question, so
   serves are not forced into FH/BH -- interim design).
3. Primary signal, ``takeback_lateral``: take the frame in the backswing
   window before contact where the racket wrist is farthest from its
   contact position (the "takeback"), and measure its position across the
   torso: projected on the axis perpendicular to hip_mid->shoulder_mid,
   relative to hip_mid, in torso lengths, signed positive toward the racket
   -hand side. >= tie margin -> forehand (racket side), <= -margin ->
   backhand (crossed body). Same idea as the sibling's ``wrist_lateral``
   read at takeback, but 2D.
4. Tie-breaker only: if the takeback signal is inside the margin, use the
   sign of the same lateral measure at contact (``contact_lateral``). If
   that is also ambiguous -> "unknown".
0. 3D cue (added 2026-10-07, used whenever world landmarks are present and
   the serve gate did not fire): mean racket-wrist offset toward the racket
   side along the hip axis in the body's horizontal frame, in metres
   (LATERAL_3D_FOREHAND_M). Pooled AUC 0.99; leave-one-clip-out 0.96 on the
   46 hits. Steps 3-4 and the gap fallback run only when the 3D cue is
   unavailable (no world landmarks, or too few valid frames before contact);
   the gap's serve veto in step 2 always applies. No label is given when the
   2D wrist/torso is missing at contact, because the serve gate cannot run.
5. Wrist gap (added 2026-10-07 after the first real-footage checks): the
   median distance between the two wrists around contact, in torso lengths.
   Below WRIST_GAP_BACKHAND both hands are on the racket (two-handed
   backhand). It is used twice: it vetoes the serve gate (step 2), and it
   answers, with fixed low confidence, whenever steps 3-4 would say
   "unknown" for any reason other than missing contact landmarks (including
   the edge-on case below). On the 46 owner-labeled hits with the hand given:
   0.61 leave-one-clip-out (0.78 in-sample, not a usable figure); the
   always-forehand baseline is 0.54. The gap window is +/-GAP_WINDOW_S, i.e.
   +/-5 frames at 30 fps. Not good enough for the 0.85 target.

2D limitation (state honestly): the sibling uses 3D world landmarks, so
torso rotation (shoulder/hip yaw) is measured directly. Here landmarks are
2D pixels, so torso rotation is NOT measured. Stand-ins used:
  * which shoulder appears on which image side (clip median) fixes which
    image direction is "racket side";
  * the wrist's lateral offset from the torso midline.
Both depend on camera angle. Behind-the-baseline / front views work best;
if the shoulders are near edge-on (side-on camera) the racket side cannot be
resolved, so the takeback rule abstains and the wrist-gap fallback (step 5)
answers with low confidence. The serve gate runs before this check. Apparent shoulder width at contact is
recorded in the result for debugging but does NOT influence the label.
Perspective, camera roll, and a player rotating through 90 degrees can all
flip the sign; none of this has been tested on real footage.

Missing data: landmarks that are NaN, below MIN_VISIBILITY, or on frames
with no detected player are treated as missing. If the racket wrist or torso
is missing around contact, or no wrist gap can be measured, the result is
"unknown" with confidence 0. If only the backswing is missing, the
wrist-gap fallback answers (confidence GAP_CONF, reason says so).
"""
from __future__ import annotations

import dataclasses
from typing import Callable, Optional, Sequence

import numpy as np
from scipy.signal import savgol_filter

from cv.pose_overlay import (
    L_HIP, L_SHOULDER, L_WRIST, MIN_VISIBILITY, NOSE, R_HIP, R_SHOULDER,
    R_WRIST, PlayerLandmarkSequence,
)
from ml.shot_timing import ShotEvent, _wrist_speed

FOREHAND, BACKHAND, OTHER, UNKNOWN = "forehand", "backhand", "other", "unknown"
LABELS = (FOREHAND, BACKHAND, OTHER)           # valid ground-truth labels
PREDICTIONS = (FOREHAND, BACKHAND, OTHER, UNKNOWN)

# --- thresholds: UNVALIDATED starting values, except WRIST_GAP_BACKHAND ----
BACK_WINDOW_S = 0.6          # backswing search window before contact
TIE_MARGIN = 0.15            # torso lengths; |lateral| below this is ambiguous
CONF_SATURATION = 0.6        # |lateral| (torso lengths) at which confidence = 1
MIN_SHOULDER_SPREAD = 0.15   # shoulder separation across torso, in torso lengths
MIN_WINDOW_FRAMES = 3        # valid backswing frames needed
SERVE_HEAD_THRESHOLD = 0.3   # wrist above nose, torso lengths (sibling value)
SERVE_HIP_THRESHOLD = 1.3    # wrist above hip mid, torso lengths (sibling value)
HAND_AMBIGUOUS_RATIO = 1.15  # peak-speed ratio below which inferred hand is shaky
# Wrist gap: distance between the two wrists, torso lengths, median over
# +/-GAP_WINDOW_S around contact. Two-handed backhands keep the wrists together;
# a forehand's off arm is away. 0.30 is the balanced-accuracy cut on the 46
# owner-labeled real hits (2026-10-07), i.e. IN-SAMPLE; leave-one-clip-out cuts
# ranged 0.28-0.64, so it is camera-dependent (docs/real-footage-findings.md).
WRIST_GAP_BACKHAND = 0.30
GAP_WINDOW_S = 0.17          # ~5 frames at 30 fps
GAP_CONF = 0.3               # fixed low confidence for gap-only answers
# 3D cue (step 0): mean racket-wrist offset along the hip axis, metres, in the
# body's own horizontal frame (MediaPipe world landmarks), over the window
# [-LAT3D_WINDOW_S, -LAT3D_GAP_S] before contact. >= threshold -> forehand.
# 0.28 m is the balanced-accuracy cut on all 46 owner-labeled hits (the same
# rule picked 0.28-0.33 m in each leave-one-clip-out fold);
# LOCO accuracy 44/46 = 0.96 (docs/real-footage-findings.md).
LATERAL_3D_FOREHAND_M = 0.28
LAT3D_WINDOW_S = 0.4
LAT3D_GAP_S = 0.07
LAT3D_CONF_SATURATION_M = 0.15   # |offset - threshold| at which confidence = 1
LAT3D_MIN_FRAMES = 3
# Contact neighbourhood (serve gate window, nearest valid contact frame), in
# seconds so 60 fps clips look at the same time span; 2 frames at 30 fps.
CONTACT_RADIUS_S = 2 / 30.0


@dataclasses.dataclass(frozen=True)
class StrokeClassification:
    """Result for one shot. ``confidence`` is a heuristic in [0, 1] derived
    from the margin -- NOT a calibrated probability."""

    label: str                       # forehand | backhand | other | unknown
    confidence: float
    margin: float                    # |decisive signal| (torso lengths); 0 if unknown
    hand: str                        # "left" | "right" (racket hand used)
    hand_source: str                 # "given" | "inferred" | "default"
    takeback_lateral: Optional[float] = None
    contact_lateral: Optional[float] = None
    wrist_above_head: Optional[float] = None
    wrist_above_hip: Optional[float] = None
    shoulder_width_ratio: Optional[float] = None   # debug only, unused by rule
    takeback_frame: Optional[int] = None
    wrist_gap: Optional[float] = None     # torso lengths around contact; see WRIST_GAP_BACKHAND
    lateral_3d: Optional[float] = None    # metres; see LATERAL_3D_FOREHAND_M
    used_tiebreak: bool = False
    reason: str = ""


# ------------------------------------------------------------ helpers
def _masked_landmarks(seq: PlayerLandmarkSequence) -> np.ndarray:
    """Landmarks with NaN wherever missing: undetected frame, low visibility."""
    lm = np.array(seq.landmarks, dtype=float)
    bad = (np.asarray(seq.visibility) < MIN_VISIBILITY) | ~np.asarray(seq.detected)[:, None]
    lm[bad] = np.nan
    return lm


def detect_racket_hand(seq: PlayerLandmarkSequence) -> tuple[Optional[str], float]:
    """Racket hand = wrist with the higher smoothed PEAK speed over the clip.

    Returns (hand, peak_ratio) where ratio = higher/lower peak (inf if the
    other wrist never moved). hand is None when neither wrist moves (no
    evidence). Known failure: a two-handed backhand-only clip, or a clip
    where the off hand is faster (e.g. toss), can flip this -- hence the
    ``hand`` override on classify_shot. Pixel speed is also camera-dependent.
    """
    lm = _masked_landmarks(seq)
    n = lm.shape[0]
    peaks = {}
    for name, idx in (("left", L_WRIST), ("right", R_WRIST)):
        sp = _wrist_speed(lm, idx, seq.fps)
        sp = np.where(np.isnan(sp), 0.0, sp)
        w = min(5, n if n % 2 == 1 else n - 1)
        if w >= 5:
            sp = savgol_filter(sp, window_length=w, polyorder=2)
        peaks[name] = float(np.max(sp)) if sp.size else 0.0
    hi, lo = max(peaks.values()), min(peaks.values())
    if hi <= 1e-9:
        return None, 1.0
    hand = "right" if peaks["right"] >= peaks["left"] else "left"
    return hand, (hi / lo if lo > 1e-9 else float("inf"))


def _unknown(hand, hand_source, reason, **kw) -> StrokeClassification:
    return StrokeClassification(label=UNKNOWN, confidence=0.0, margin=0.0,
                                hand=hand, hand_source=hand_source,
                                reason=reason, **kw)


def _wrist_gap(lm: np.ndarray, c: int, fps: float) -> Optional[float]:
    """Median distance between the wrists (torso lengths) in +/-GAP_WINDOW_S of c."""
    k = max(1, int(round(GAP_WINDOW_S * fps)))
    w = slice(max(0, c - k), min(lm.shape[0], c + k + 1))
    torso = np.linalg.norm((lm[w, L_SHOULDER] + lm[w, R_SHOULDER]) / 2
                           - (lm[w, L_HIP] + lm[w, R_HIP]) / 2, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        gap = np.linalg.norm(lm[w, L_WRIST] - lm[w, R_WRIST], axis=1) / torso
    gap = gap[np.isfinite(gap)]
    return float(np.median(gap)) if gap.size else None


def _lateral_3d(seq: PlayerLandmarkSequence, c: int, hand: str) -> Optional[float]:
    """Mean racket-wrist offset (m) toward the racket side along the hip axis.

    World landmarks are hip-centred metres, so this does not depend on how
    large the player is in the image. Their axes follow the CAMERA, not
    gravity: zeroing y projects onto the camera's x-z plane, which is only
    horizontal for a roughly level camera (all 7 test clips; a tilted rig is
    untested). Frames that are undetected or below MIN_VISIBILITY (the same
    mask as the 2D path) are skipped. None without world landmarks or with
    fewer than LAT3D_MIN_FRAMES frames.
    """
    world = getattr(seq, "world_landmarks", None)
    if world is None:
        return None
    w = np.asarray(world, dtype=float)
    lo = max(0, c - int(round(LAT3D_WINDOW_S * seq.fps)))
    hi = min(w.shape[0], c - int(round(LAT3D_GAP_S * seq.fps)) + 1)
    wrist_idx = R_WRIST if hand == "right" else L_WRIST
    side = 1.0 if hand == "right" else -1.0
    vals = []
    for f in range(lo, hi):
        if not np.asarray(seq.detected)[f] or np.max(np.asarray(seq.visibility)[f]) < MIN_VISIBILITY:
            continue
        pts = w[f, [L_HIP, R_HIP, wrist_idx]]
        if not np.all(np.isfinite(pts)):
            continue
        axis = pts[1] - pts[0]
        axis[1] = 0.0
        n = np.linalg.norm(axis)
        if n < 1e-6:
            continue
        vals.append(side * float(np.dot(pts[2] - (pts[0] + pts[1]) / 2, axis / n)))
    return float(np.mean(vals)) if len(vals) >= LAT3D_MIN_FRAMES else None


def _gap_fallback(unknown: StrokeClassification, gap: Optional[float]) -> StrokeClassification:
    """Replace an "unknown" with a low-confidence FH/BH from the wrist gap."""
    if gap is None:
        return dataclasses.replace(unknown, wrist_gap=None)
    label = BACKHAND if gap < WRIST_GAP_BACKHAND else FOREHAND
    return dataclasses.replace(unknown, label=label, confidence=GAP_CONF,
                               margin=abs(gap - WRIST_GAP_BACKHAND), wrist_gap=gap,
                               reason=f"wrist-gap fallback ({unknown.reason})")


def _near_valid(arr: np.ndarray, c: int, radius: int = 2) -> Optional[int]:
    """Closest frame to c (within radius) whose row is fully finite."""
    for d in range(radius + 1):
        for f in (c - d, c + d):
            if 0 <= f < arr.shape[0] and np.all(np.isfinite(arr[f])):
                return f
    return None


# ------------------------------------------------------------ classifier
def classify_shot(
    seq: PlayerLandmarkSequence,
    event: ShotEvent,
    hand: Optional[str] = None,
    *,
    _inferred: Optional[tuple[Optional[str], float]] = None,
) -> StrokeClassification:
    """Classify one detected shot as forehand / backhand / other / unknown.

    Args:
        seq: landmarks for the whole clip (2D pixels, plus optional world
            landmarks used by the 3D cue).
        event: shot from ``detect_shots``; only ``contact_frame`` is used
            (``event.wrist`` is intentionally ignored -- see module docs).
        hand: "left" or "right" racket hand; inferred from the clip when None.
    """
    if hand not in (None, "left", "right"):
        raise ValueError(f"hand must be 'left', 'right' or None, got {hand!r}")

    if hand is None:
        inferred_hand, ratio = _inferred if _inferred is not None else detect_racket_hand(seq)
        if inferred_hand is None:
            hand, src, ratio = "right", "default", 1.0
        else:
            hand, src = inferred_hand, "inferred"
    else:
        src, ratio = "given", float("inf")

    rw_idx = R_WRIST if hand == "right" else L_WRIST
    lm = _masked_landmarks(seq)
    n, c = lm.shape[0], int(event.contact_frame)
    if not (0 <= c < n):
        return _unknown(hand, src, f"contact_frame {c} outside clip (0..{n - 1})")
    gap = _wrist_gap(lm, c, seq.fps)
    lat3 = _lateral_3d(seq, c, hand)
    result = _classify_rule(seq, lm, c, n, hand, src, ratio, rw_idx, gap)
    contact_missing = result.reason.startswith("racket wrist/torso missing")
    if result.label == OTHER or contact_missing:  # serve gate wins; no contact pose -> no guess
        return dataclasses.replace(result, wrist_gap=gap, lateral_3d=lat3)
    if lat3 is not None:                         # step 0: 3D cue when world landmarks exist
        margin = lat3 - LATERAL_3D_FOREHAND_M
        conf = _scale_conf(min(1.0, abs(margin) / LAT3D_CONF_SATURATION_M), src, ratio)
        return dataclasses.replace(
            result, label=FOREHAND if margin >= 0 else BACKHAND, confidence=conf,
            margin=abs(margin), wrist_gap=gap, lateral_3d=lat3, used_tiebreak=False,
            reason="3D racket-wrist offset along hip axis (world landmarks)")
    if result.label == UNKNOWN:
        return _gap_fallback(result, gap)
    return dataclasses.replace(result, wrist_gap=gap)


def _classify_rule(seq, lm, c, n, hand, src, ratio, rw_idx, gap) -> StrokeClassification:
    """The original takeback-side rule (module docstring), plus the gap serve veto."""

    sh_l, sh_r = lm[:, L_SHOULDER], lm[:, R_SHOULDER]
    hip_l, hip_r = lm[:, L_HIP], lm[:, R_HIP]
    wrist, nose = lm[:, rw_idx], lm[:, NOSE]
    sm, hm = (sh_l + sh_r) / 2, (hip_l + hip_r) / 2
    axis = sm - hm                                   # hip -> shoulder, "up" along torso
    torso = np.linalg.norm(axis, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        u = np.stack([-axis[:, 1], axis[:, 0]], axis=1) / torso[:, None]   # (1,0) when upright
        spread = np.sum((sh_r - sh_l) * u, axis=1) / torso   # right-minus-left shoulder, torso lengths

    r = max(1, int(round(CONTACT_RADIUS_S * seq.fps)))
    fc = _near_valid(np.concatenate([wrist, hm, torso[:, None]], axis=1), c, radius=r)
    if fc is None:
        return _unknown(hand, src, "racket wrist/torso missing around contact")

    # --- overhead / serve gate
    hi = slice(max(0, c - r), min(n, c + r + 1))
    with np.errstate(invalid="ignore", divide="ignore"):
        above_head_s = (nose[:, 1] - wrist[:, 1]) / torso
        above_hip_s = (hm[:, 1] - wrist[:, 1]) / torso
    ah = float(np.nanmax(above_head_s[hi])) if np.any(np.isfinite(above_head_s[hi])) else None
    ahip = float(np.nanmax(above_hip_s[hi])) if np.any(np.isfinite(above_hip_s[hi])) else None
    with np.errstate(invalid="ignore"):
        swc = float(np.hypot(*(sh_r[fc] - sh_l[fc])) / torso[fc]) if np.all(np.isfinite(sh_r[fc] - sh_l[fc])) else None
    common = dict(wrist_above_head=ah, wrist_above_hip=ahip, shoulder_width_ratio=swc)

    ex_head = (ah - SERVE_HEAD_THRESHOLD) / 0.3 if ah is not None else -1.0
    ex_hip = (ahip - SERVE_HIP_THRESHOLD) / 0.5 if ahip is not None else -1.0
    # Serve veto: at a serve contact the off arm is down, far from the racket
    # wrist; both wrists together means a two-handed groundstroke reaching high.
    two_hands = gap is not None and gap < WRIST_GAP_BACKHAND
    if max(ex_head, ex_hip) > 0 and not two_hands:
        score = float(min(1.0, max(ex_head, ex_hip)))
        return StrokeClassification(OTHER, _scale_conf(score, src, ratio), score, hand, src,
                                    reason="overhead/serve-like: racket wrist high at contact "
                                    "(interim 'other' class; scope pending product-manager)",
                                    **common)

    # --- racket side in the image (after the serve gate, so side-on serves stay "other")
    if not np.any(np.isfinite(spread)):
        return _unknown(hand, src, "torso landmarks missing for whole clip", **common)
    med_spread = float(np.nanmedian(spread))
    if abs(med_spread) < MIN_SHOULDER_SPREAD:
        return _unknown(hand, src, "shoulders near edge-on to camera; racket side unresolvable "
                        "(camera-angle limit of 2D cues)", **common)
    # image direction (along u) of the racket side, then sign so lateral > 0 = racket side
    racket_sign = (1.0 if med_spread > 0 else -1.0) * (1.0 if hand == "right" else -1.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        lat = np.sum((wrist - hm) * u, axis=1) * racket_sign / torso

    # --- takeback frame: farthest from contact position within the window
    lo = max(0, c - int(round(BACK_WINDOW_S * seq.fps)))
    cand = np.arange(lo, c)
    ok = np.array([f for f in cand if np.all(np.isfinite(wrist[f])) and np.isfinite(lat[f])], dtype=int)
    contact_lat_vals = lat[max(0, c - 1):min(n, c + 2)]
    contact_lat = float(np.nanmedian(contact_lat_vals)) if np.any(np.isfinite(contact_lat_vals)) else None
    if len(ok) < MIN_WINDOW_FRAMES:
        return _unknown(hand, src, f"only {len(ok)} valid backswing frames (need {MIN_WINDOW_FRAMES})",
                        contact_lateral=contact_lat, **common)
    dist = np.linalg.norm(wrist[ok] - wrist[fc], axis=1)
    tb = int(ok[int(np.argmax(dist))])
    vals = lat[max(0, tb - 1):tb + 2]
    tb_lat = float(np.nanmean(vals))

    base = dict(takeback_lateral=tb_lat, contact_lateral=contact_lat, takeback_frame=tb, **common)
    if abs(tb_lat) >= TIE_MARGIN:
        label = FOREHAND if tb_lat > 0 else BACKHAND
        return StrokeClassification(label, _scale_conf(min(1.0, abs(tb_lat) / CONF_SATURATION), src, ratio),
                                    abs(tb_lat), hand, src, reason="takeback side of torso", **base)
    if contact_lat is not None and abs(contact_lat) >= TIE_MARGIN:
        label = FOREHAND if contact_lat > 0 else BACKHAND
        conf = 0.5 * min(1.0, abs(contact_lat) / CONF_SATURATION)
        return StrokeClassification(label, _scale_conf(conf, src, ratio), abs(contact_lat), hand, src,
                                    used_tiebreak=True,
                                    reason="takeback ambiguous; tie-broken by contact-frame side", **base)
    return StrokeClassification(UNKNOWN, 0.0, max(abs(tb_lat), abs(contact_lat or 0.0)), hand, src,
                                reason="takeback and contact both near torso midline", **base)


def _scale_conf(conf: float, src: str, ratio: float) -> float:
    """Halve confidence when the racket hand was inferred with weak evidence."""
    if src == "default" or (src == "inferred" and ratio < HAND_AMBIGUOUS_RATIO):
        conf *= 0.5
    return float(conf)


def classify_shots(
    seq: PlayerLandmarkSequence,
    events: Sequence[ShotEvent],
    hand: Optional[str] = None,
    classifier: Callable[..., StrokeClassification] = classify_shot,
) -> list[StrokeClassification]:
    """Classify every shot in a clip; result is aligned 1:1 with ``events``.

    The racket hand is resolved ONCE per clip (given, else inferred from
    whole-clip peak wrist speed) so a single clip never mixes hands.
    ``classifier`` lets a future learned model replace the rule with the
    same ``(seq, event, hand) -> StrokeClassification`` signature.
    """
    if hand is None and classifier is classify_shot:
        inferred = detect_racket_hand(seq)
        return [classify_shot(seq, e, None, _inferred=inferred) for e in events]
    if hand is None:
        h, _ = detect_racket_hand(seq)
        hand = h or "right"
    return [classifier(seq, e, hand) for e in events]
