"""Turn raw landmarks into the kinematic time series the rest of the tool reads.

Every length is divided by the subject's torso length so that metrics are
comparable between players of different heights and between clips shot from
different distances. Angles are in degrees, speeds in torso-lengths/second.
`Features.scale` carries the metres-per-torso-length factor, so anything that
needs real-world units (m/s, km/h) can convert back.
"""
from __future__ import annotations

import dataclasses

import numpy as np

from . import geometry as geo
from . import pose as P


@dataclasses.dataclass
class Features:
    """Named kinematic time series for one video, all of length T."""

    series: dict[str, np.ndarray]
    points: dict[str, np.ndarray]  # (T, 3) trajectories in torso units
    hand: str                      # "right" or "left" (racket hand)
    fps: float
    scale: float                   # metres per torso unit
    two_handed: bool

    def __getitem__(self, key: str) -> np.ndarray:
        return self.series[key]

    @property
    def n_frames(self) -> int:
        return len(next(iter(self.series.values())))


def detect_handedness(seq: P.PoseSequence) -> str:
    """Guess the racket hand as the wrist that reaches the higher peak speed.

    Peak speed, not total distance travelled: a serve's toss arm swings the
    ball up and then follows all the way back down on every single point, so
    over a whole clip it can rack up as much *path length* as the racket arm
    does -- confirmed on real serve footage where it outright exceeded the
    racket hand's, flipping an earlier path-length heuristic onto the wrong
    hand for every metric downstream. Peak *speed* doesn't have that failure
    mode: releasing a toss is a gentle motion regardless of how far the arm
    travels, while the racket hand's swing-through is a genuine athletic
    acceleration no toss comes close to matching.
    """
    def peak_speed(idx: int) -> float:
        s = geo.smooth(geo.speed(seq.world[:, idx], seq.fps), window=5, poly=2)
        return float(s.max()) if s.size else 0.0

    return "right" if peak_speed(P.R_WRIST) >= peak_speed(P.L_WRIST) else "left"


def detect_two_handed(seq: P.PoseSequence, hand: str) -> bool:
    """True when both wrists stay close together through the fastest motion.

    Measured only over the top quartile of racket-hand speed, i.e. during the
    actual swings, because the hands naturally separate while waiting.
    """
    scale = seq.torso_scale
    racket = P.R_WRIST if hand == "right" else P.L_WRIST
    other = P.L_WRIST if hand == "right" else P.R_WRIST

    speed = geo.speed(seq.world[:, racket], seq.fps)
    if speed.size < 4:
        return False
    fast = speed >= np.quantile(speed, 0.75)
    gap = np.linalg.norm(seq.world[:, racket] - seq.world[:, other], axis=-1) / scale
    return bool(np.median(gap[fast]) < 0.55)


def extract_features(seq: P.PoseSequence, hand: str | None = None) -> Features:
    """Build every time series the scorers and measurers read."""
    scale = seq.torso_scale
    hand = hand or detect_handedness(seq)
    two_handed = detect_two_handed(seq, hand)

    wr = P.R_WRIST if hand == "right" else P.L_WRIST
    el = P.R_ELBOW if hand == "right" else P.L_ELBOW
    sh = P.R_SHOULDER if hand == "right" else P.L_SHOULDER
    off_wr = P.L_WRIST if hand == "right" else P.R_WRIST

    # Everything below is in torso lengths; `scale` converts back to metres.
    w = seq.world / scale

    shoulder_mid = (w[:, P.L_SHOULDER] + w[:, P.R_SHOULDER]) / 2.0
    hip_mid = (w[:, P.L_HIP] + w[:, P.R_HIP]) / 2.0
    ankle_mid = (w[:, P.L_ANKLE] + w[:, P.R_ANKLE]) / 2.0
    head = w[:, P.NOSE]

    # --- rotations ---------------------------------------------------------
    shoulder_yaw = geo.unwrap_deg(geo.ground_yaw(w[:, P.L_SHOULDER], w[:, P.R_SHOULDER]))
    hip_yaw = geo.unwrap_deg(geo.ground_yaw(w[:, P.L_HIP], w[:, P.R_HIP]))
    x_factor = geo.wrap_deg(shoulder_yaw - hip_yaw)
    shoulder_yaw_vel = np.abs(geo.derivative(shoulder_yaw, seq.fps))
    hip_yaw_vel = np.abs(geo.derivative(hip_yaw, seq.fps))

    # --- arm ---------------------------------------------------------------
    wrist_speed = geo.speed(w[:, wr], seq.fps)
    elbow_angle = geo.angle_3p(w[:, sh], w[:, el], w[:, wr])

    # --- legs --------------------------------------------------------------
    # Flexion, not joint angle: 0 is a straight leg, larger means deeper bend,
    # which is the direction every coaching cue talks in.
    knee_l = 180.0 - geo.angle_3p(w[:, P.L_HIP], w[:, P.L_KNEE], w[:, P.L_ANKLE])
    knee_r = 180.0 - geo.angle_3p(w[:, P.R_HIP], w[:, P.R_KNEE], w[:, P.R_ANKLE])
    knee_mean = (knee_l + knee_r) / 2.0
    stance_width = np.linalg.norm(w[:, P.L_ANKLE] - w[:, P.R_ANKLE], axis=-1)
    shoulder_width = np.linalg.norm(w[:, P.L_SHOULDER] - w[:, P.R_SHOULDER], axis=-1)

    # --- trunk -------------------------------------------------------------
    # Unsigned tilt from vertical. Known limitation: this cannot tell a forward
    # hunch from a backward arch, which matters for the serve's `trunk_arch`.
    trunk = shoulder_mid - hip_mid
    trunk_lean = geo.angle_between(trunk, np.array([0.0, -1.0, 0.0]))

    # --- heights (up-positive, torso lengths) ------------------------------
    wrist_above_hip = -(w[:, wr][:, 1] - hip_mid[:, 1])
    wrist_above_shoulder = -(w[:, wr][:, 1] - shoulder_mid[:, 1])
    wrist_above_head = -(w[:, wr][:, 1] - head[:, 1])
    off_wrist_above_head = -(w[:, off_wr][:, 1] - head[:, 1])

    # --- lateral position --------------------------------------------------
    # Signed position of the racket hand across the body, along the hip line,
    # positive on the racket-hand side. This is what separates a forehand from
    # a backhand.
    hip_axis = geo.unit(w[:, P.R_HIP] - w[:, P.L_HIP])
    side_sign = 1.0 if hand == "right" else -1.0
    wrist_lateral = np.sum((w[:, wr] - hip_mid) * hip_axis, axis=-1) * side_sign

    # --- centre of mass ----------------------------------------------------
    # Hip height above the ankles, not the hip's own world-frame height:
    # MediaPipe's world landmarks re-centre their origin on the hip every
    # frame, so the raw hip position is ~0 throughout and carries no signal.
    hip_height = -(hip_mid[:, 1] - ankle_mid[:, 1])
    com_rise_speed = geo.derivative(hip_height, seq.fps)

    series = {
        "shoulder_yaw": shoulder_yaw,
        "hip_yaw": hip_yaw,
        "x_factor": x_factor,
        "shoulder_yaw_vel": shoulder_yaw_vel,
        "hip_yaw_vel": hip_yaw_vel,
        "wrist_speed": wrist_speed,
        "elbow_angle": elbow_angle,
        "knee_mean": knee_mean,
        "stance_width": stance_width,
        "shoulder_width": shoulder_width,
        "trunk_lean": trunk_lean,
        "wrist_above_hip": wrist_above_hip,
        "wrist_above_shoulder": wrist_above_shoulder,
        "wrist_above_head": wrist_above_head,
        "off_wrist_above_head": off_wrist_above_head,
        "wrist_lateral": wrist_lateral,
        "hip_height": hip_height,
        "com_rise_speed": com_rise_speed,
    }

    points = {
        "wrist": w[:, wr],
        "elbow": w[:, el],
        "shoulder": w[:, sh],
        "off_wrist": w[:, off_wr],
        "hip_mid": hip_mid,
        "shoulder_mid": shoulder_mid,
        "ankle_mid": ankle_mid,
        "hip_rel_ankle": hip_mid - ankle_mid,
        "head": head,
    }

    return Features(series=series, points=points, hand=hand, fps=seq.fps,
                    scale=scale, two_handed=two_handed)
