"""Build synthetic PoseSequences with precisely controlled swing mechanics.

Positions are driven by normal-CDF ramps, so each transition's velocity profile
is an exact Gaussian. That gives two properties the tests rely on:

  * the wrist speed peaks exactly at the nominal contact time, and
  * the value at contact is exactly the midpoint of the ramp,

which means a test can request "contact 0.45 torso lengths in front" and then
assert that the pipeline measures 0.45 back.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm

from tennis_coach import pose as P

TORSO = 0.50           # metres from mid-hip to mid-shoulder
SHOULDER_HALF = 0.20
HIP_HALF = 0.15
LEG = 0.90
FPS = 30.0

# Normalised swing timeline (fractions of the swing window).
T_BACK, S_BACK = 0.30, 0.060      # backswing ramp centre / width
T_HIP, S_FWD = 0.655, 0.045       # hips fire first ...
T_SHOULDER = 0.688                # ... then shoulders ...
T_CONTACT = 0.72                  # ... then the hand: contact.
T_FINISH, S_FINISH = 0.88, 0.050  # racket rises to the finish position
# Recovery happens during the gap between swings. It is deliberately wide so
# its velocity peak stays well below the contact peak and is never mistaken
# for a swing of its own.
T_RECOVER, S_RECOVER = 1.05, 0.250


def _ramp(t: np.ndarray, centre: float, width: float) -> np.ndarray:
    """Smooth 0 -> 1 transition whose derivative is a Gaussian at ``centre``."""
    return norm.cdf((t - centre) / width)


def _bend_point(a: np.ndarray, b: np.ndarray, angle_deg: float,
                axis: np.ndarray) -> np.ndarray:
    """Point p on the perpendicular bisector of a-b with angle(a, p, b) = angle.

    Used to place elbows and knees at an exact joint angle.
    """
    d = b - a
    dist = np.linalg.norm(d)
    if dist < 1e-9:
        return a.copy()

    theta = np.radians(np.clip(angle_deg, 1.0, 179.0))
    r = dist / np.sqrt(2.0 * (1.0 - np.cos(theta)))
    h = np.sqrt(max(r * r - (dist / 2.0) ** 2, 0.0))

    # Component of `axis` perpendicular to the a-b segment.
    u = d / dist
    perp = axis - np.dot(axis, u) * u
    n = np.linalg.norm(perp)
    perp = perp / n if n > 1e-9 else np.array([0.0, 0.0, -1.0])

    return a + d / 2.0 + perp * h


def synth_swing_sequence(
    *,
    n_swings: int = 1,
    swing_frames: int = 60,
    pad_frames: int = 50,
    hand: str = "right",
    shoulder_turn: float = 90.0,
    knee_peak: float = 40.0,
    contact_front: float = 0.45,      # torso lengths ahead of the hips
    takeback_lateral: float = 0.50,   # +ve = racket side (forehand), -ve = across
    contact_height: float = 0.60,     # torso lengths above the hips
    finish_height: float = 0.35,      # torso lengths above the shoulders
    elbow_contact: float = 140.0,
    stance_ratio: float = 1.6,
    weight_transfer: float = 0.25,    # torso lengths of hip travel
    trunk_lean: float = 8.0,
    overhead: bool = False,           # emulate a serve
    fps: float = FPS,
) -> P.PoseSequence:
    """Generate a landmark sequence for one or more idealised swings."""
    per = swing_frames + pad_frames
    total = pad_frames + n_swings * per
    world = np.zeros((total, P.NUM_LANDMARKS, 3))

    take_x, take_h = -0.35, 0.10     # torso units at the top of the backswing
    lean_axis = np.array([0.0, 0.0, -1.0])

    # Ramp amplitudes chosen so the value at contact is exactly the requested
    # one: the forward ramp is at its midpoint there, so it must carry twice
    # the distance from the takeback position to the contact position.
    x_fwd = 2.0 * (contact_front - take_x)
    h_fwd = 2.0 * (contact_height - take_h)
    # Serves finish overhead; groundstrokes finish `finish_height` above the
    # shoulders, which sit exactly one torso length above the hips.
    h_target = (2.0 if overhead else 1.0) + finish_height
    h_fin = h_target - take_h - h_fwd
    yaw_finish = shoulder_turn + 35.0
    # A left-hander's forehand sits on their left, i.e. the opposite side of
    # the hip axis, which points to the player's right.
    side_sign = 1.0 if hand == "right" else -1.0

    for f in range(total):
        local = f - pad_frames
        t = 0.0 if local < 0 else (local % per) / float(swing_frames)

        back = _ramp(np.array(t), T_BACK, S_BACK).item()
        fwd_hand = _ramp(np.array(t), T_CONTACT, S_FWD).item()
        fwd_hip = _ramp(np.array(t), T_HIP, S_FWD).item()
        fwd_shoulder = _ramp(np.array(t), T_SHOULDER, S_FWD).item()
        fin = _ramp(np.array(t), T_FINISH, S_FINISH).item()
        # Returns every quantity to its ready value before the next swing.
        rec = _ramp(np.array(t), T_RECOVER, S_RECOVER).item()

        # --- rotations (degrees) ------------------------------------------
        shoulder_yaw = (-shoulder_turn * back + yaw_finish * fwd_shoulder
                        - (yaw_finish - shoulder_turn) * rec)
        hip_turn = shoulder_turn * 0.65
        hip_finish = hip_turn + 30.0
        hip_yaw = (-hip_turn * back + hip_finish * fwd_hip
                   - (hip_finish - hip_turn) * rec)

        # --- global body translation & posture ----------------------------
        hip_x = weight_transfer * TORSO * (fwd_hip - rec)
        hip_y = -0.04 * TORSO * (fwd_hip - rec)   # slight rise from leg drive
        hip_mid = np.array([hip_x, hip_y, 0.0])

        lean = np.radians(trunk_lean * (fwd_hand - rec))
        shoulder_mid = hip_mid + np.array([np.sin(lean) * TORSO, -np.cos(lean) * TORSO, 0.0])

        sy, hy = np.radians(shoulder_yaw), np.radians(hip_yaw)
        s_dir = np.array([np.cos(sy), 0.0, np.sin(sy)])
        h_dir = np.array([np.cos(hy), 0.0, np.sin(hy)])

        # --- racket-hand wrist --------------------------------------------
        x = (take_x * back + x_fwd * fwd_hand
             - (take_x + x_fwd) * rec) * TORSO
        above_hip = (take_h * back + h_fwd * fwd_hand + h_fin * fin
                     - h_target * rec) * TORSO

        # Lateral offset follows the hips so it stays on the player's own
        # left-right axis as the body rotates. Tied to the (earlier-peaking)
        # hip ramp rather than the hand ramp so the sideways unwind is mostly
        # resolved before contact, same as a real swing: the hand's lateral
        # position stabilises before the hitting zone, it does not keep
        # sweeping sideways through contact. Left coupled to `fwd_hand` the
        # two ramps peak at the same instant and contact-in-front measurements
        # end up dominated by this unwind instead of the true forward motion.
        lateral = takeback_lateral * side_sign * (back - fwd_hip) * TORSO
        wrist = (np.array([hip_mid[0] + x, hip_mid[1] - above_hip, 0.10])
                 + lateral * h_dir)

        # --- knees ---------------------------------------------------------
        flexion = 12.0 + (knee_peak - 12.0) * np.exp(-((t - 0.52) ** 2) / (2 * 0.13 ** 2))
        stance_half = stance_ratio * (2 * SHOULDER_HALF) / 2.0

        lm = np.zeros((P.NUM_LANDMARKS, 3))
        lm[P.NOSE] = shoulder_mid + np.array([0.0, -0.26, 0.0])
        lm[P.L_EAR] = lm[P.NOSE] + np.array([-0.07, -0.02, 0.0])
        lm[P.R_EAR] = lm[P.NOSE] + np.array([0.07, -0.02, 0.0])
        lm[P.R_SHOULDER] = shoulder_mid + SHOULDER_HALF * s_dir
        lm[P.L_SHOULDER] = shoulder_mid - SHOULDER_HALF * s_dir
        lm[P.R_HIP] = hip_mid + HIP_HALF * h_dir
        lm[P.L_HIP] = hip_mid - HIP_HALF * h_dir

        for side, sign in (("R", 1.0), ("L", -1.0)):
            hip_i = P.R_HIP if side == "R" else P.L_HIP
            knee_i = P.R_KNEE if side == "R" else P.L_KNEE
            ankle_i = P.R_ANKLE if side == "R" else P.L_ANKLE
            heel_i = P.R_HEEL if side == "R" else P.L_HEEL
            foot_i = P.R_FOOT if side == "R" else P.L_FOOT

            # Planted at a fixed stance, not tracking hip_mid's own horizontal
            # drift: a real foot stays put on the court while the hip shifts
            # forward over it during weight transfer. Tying the ankle to the
            # hip's current x would make hip-relative-to-ankle constant no
            # matter how much weight_transfer moves the hip, which is exactly
            # the real MediaPipe degeneracy this fixture exists to catch.
            ankle = np.array([sign * stance_half, hip_mid[1] + LEG, 0.0])
            lm[ankle_i] = ankle
            lm[knee_i] = _bend_point(lm[hip_i], ankle, 180.0 - flexion, lean_axis)
            lm[heel_i] = ankle + np.array([0.0, 0.02, 0.08])
            lm[foot_i] = ankle + np.array([0.0, 0.02, -0.16])

        wrist_i = P.R_WRIST if hand == "right" else P.L_WRIST
        elbow_i = P.R_ELBOW if hand == "right" else P.L_ELBOW
        shoulder_i = P.R_SHOULDER if hand == "right" else P.L_SHOULDER
        off_wrist_i = P.L_WRIST if hand == "right" else P.R_WRIST
        off_elbow_i = P.L_ELBOW if hand == "right" else P.R_ELBOW
        off_shoulder_i = P.L_SHOULDER if hand == "right" else P.R_SHOULDER

        lm[wrist_i] = wrist
        lm[elbow_i] = _bend_point(lm[shoulder_i], wrist, elbow_contact, lean_axis)

        # Non-racket hand: parked for a groundstroke, tossing for a serve.
        if overhead:
            toss = 0.75 * TORSO * (back - rec)
            lm[off_wrist_i] = lm[off_shoulder_i] + np.array([0.0, -0.42 - toss, 0.05])
        else:
            lm[off_wrist_i] = shoulder_mid + np.array([-0.34 * side_sign, 0.16, -0.12])
        lm[off_elbow_i] = (lm[off_shoulder_i] + lm[off_wrist_i]) / 2.0 + np.array([0.0, 0.0, -0.08])

        for w_i, i_i, t_i, p_i in (
            (P.R_WRIST, P.R_INDEX, P.R_THUMB, P.R_PINKY),
            (P.L_WRIST, P.L_INDEX, P.L_THUMB, P.L_PINKY),
        ):
            lm[i_i] = lm[w_i] + np.array([0.0, -0.06, 0.0])
            lm[t_i] = lm[w_i] + np.array([0.0, -0.03, 0.02])
            lm[p_i] = lm[w_i] + np.array([0.0, -0.05, 0.02])
        for eye in (1, 2, 3, 4, 5, 6, 9, 10):
            lm[eye] = lm[P.NOSE]

        world[f] = lm

    # Project to pixels with a simple pinhole-ish scaling.
    k = 320.0
    pixel = np.stack([
        640.0 + world[:, :, 0] * k,
        360.0 + world[:, :, 1] * k,
    ], axis=-1)

    return P.PoseSequence(
        world=world, pixel=pixel,
        visibility=np.ones((total, P.NUM_LANDMARKS)),
        detected=np.ones(total, bool),
        fps=fps, width=1280, height=720,
    )
