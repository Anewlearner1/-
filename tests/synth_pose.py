"""Synthetic PlayerLandmarkSequence fixtures for ml/shot_timing.py tests.

Same normal-CDF-ramp technique as the sibling tennis-form-coach project's
tests/synth.py (see its docstring): a position that follows a normal-CDF
ramp has a velocity that is *exactly* a Gaussian, so the speed signal peaks
at precisely the requested frame -- giving an exact, hand-computable ground
truth for "how many swings, and when" instead of eyeballing a recording.

This is a fresh, much simpler implementation for Rally AI's needs (pixel-
space wrist position only, no 3D body/joint geometry) -- no code imported
from tennis-form-coach, which is a separate repo.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm

from cv.pose_overlay import L_WRIST, NUM_LANDMARKS, R_WRIST, PlayerLandmarkSequence


def _ramp(t: np.ndarray, centre: float, width: float) -> np.ndarray:
    """Smooth 0 -> 1 transition whose derivative is a Gaussian at ``centre``."""
    return norm.cdf((t - centre) / width)


def synth_landmark_sequence(
    contact_frames: list[int],
    *,
    n_frames: int | None = None,
    fps: float = 30.0,
    width_frames: float = 3.0,
    recovery_width_ratio: float = 8.0,
    recovery_offset_frames: float = 25.0,
    amplitude_px: float = 300.0,
    wrist_idx: int = R_WRIST,
    rest_pos: tuple[float, float] = (640.0, 500.0),
    frame_pad: int = 40,
) -> PlayerLandmarkSequence:
    """Builds a landmark sequence whose wrist-speed signal has one clean,
    exactly-timed peak per entry in ``contact_frames``.

    Each "swing" is one forward normal-CDF ramp (out to full extension,
    centred exactly at the requested contact frame) followed by a much
    wider, lower-peak-velocity ramp back to rest (``recovery_width_ratio``
    controls how much lower -- default 8x width gives a recovery peak about
    1/8 the height of the contact peak, well under
    ``detect_shots``'s default ``min_peak_ratio=0.35`` so it is not mistaken
    for a second swing).

    Only ``wrist_idx`` moves; the off-hand wrist and every other landmark
    stay fixed at a rest position, fully visible and "detected" every frame
    (this fixture is about the timing signal, not detection dropout -- see
    ``tests/test_shot_timing.py`` for a dropout-robustness case built
    separately on top of this).
    """
    contact_frames = sorted(contact_frames)
    if n_frames is None:
        last = contact_frames[-1] if contact_frames else 0
        n_frames = int(last + recovery_offset_frames + frame_pad)

    t = np.arange(n_frames, dtype=float)
    recovery_width = width_frames * recovery_width_ratio

    # A 2D unit direction (mostly horizontal, slight vertical component) so
    # the swing isn't degenerately axis-aligned.
    direction = np.array([1.0, -0.3])
    direction = direction / np.linalg.norm(direction)

    offset = np.zeros(n_frames)
    for c in contact_frames:
        out_ramp = _ramp(t, c, width_frames)
        back_ramp = _ramp(t, c + recovery_offset_frames, recovery_width)
        offset += amplitude_px * (out_ramp - back_ramp)

    x = rest_pos[0] + offset * direction[0]
    y = rest_pos[1] + offset * direction[1]

    landmarks = np.zeros((n_frames, NUM_LANDMARKS, 2))
    landmarks[:, :, 0] = rest_pos[0]
    landmarks[:, :, 1] = rest_pos[1]
    landmarks[:, wrist_idx, 0] = x
    landmarks[:, wrist_idx, 1] = y

    visibility = np.ones((n_frames, NUM_LANDMARKS))
    detected = np.ones(n_frames, dtype=bool)

    return PlayerLandmarkSequence(
        landmarks=landmarks,
        visibility=visibility,
        detected=detected,
        fps=fps,
        width=1280,
        height=720,
    )


# ---------------------------------------------------------------------------
# Stroke-type fixture for ml/stroke_classification.py tests.
#
# SYNTHETIC: encodes our own assumption about what a forehand / backhand /
# overhead looks like in 2D (racket-side takeback vs cross-body takeback vs
# wrist above head). It can only verify the classifier's logic matches that
# assumption -- it says nothing about how real footage looks or how accurate
# the classifier is on it.
# ---------------------------------------------------------------------------
from cv.pose_overlay import (  # noqa: E402
    L_HIP, L_SHOULDER, NOSE, R_HIP, R_SHOULDER,
)

TORSO_LEN = 120.0
HIP_MID = (640.0, 520.0)

# (lateral offset from torso midline in torso lengths, positive = racket side;
#  y pixel) for takeback and for the position at contact.
STROKE_SHAPES = {
    "forehand": {"takeback": (0.9, 500.0), "contact": (0.5, 470.0)},
    "backhand": {"takeback": (-0.8, 500.0), "contact": (-0.4, 470.0)},
    # overhead: wrist far above the nose (y=350) at contact; takeback lateral
    # deliberately cross-body to prove the height gate overrides lateral side.
    "overhead": {"takeback": (-0.6, 380.0), "contact": (0.1, 250.0)},
}


def synth_stroke_sequence(
    strokes: list[tuple[int, str]],
    *,
    hand: str = "right",
    facing: str = "camera",
    n_frames: int | None = None,
    fps: float = 30.0,
    off_wrist_faster: bool = False,
) -> PlayerLandmarkSequence:
    """Landmark sequence with one stroke per ``(contact_frame, kind)``.

    ``kind`` in STROKE_SHAPES. The racket wrist goes rest -> takeback (slow)
    -> through contact (fast, speed peak exactly at contact_frame) -> follow
    through -> rest. ``facing="camera"`` puts the player's right shoulder on
    image-left; ``"away"`` mirrors it. ``off_wrist_faster`` makes the OFF
    wrist also swing (a mirrored fast motion with 1.5x amplitude) so that
    detect_shots' per-event ``wrist`` flips to the off hand while the true
    stroke type is unchanged.
    """
    strokes = sorted(strokes)
    if n_frames is None:
        n_frames = strokes[-1][0] + 70
    t = np.arange(n_frames, dtype=float)

    # image-x direction of the racket side
    right_dir = -1.0 if facing == "camera" else 1.0      # image dir of player's right
    racket_dir = right_dir if hand == "right" else -right_dir

    def to_px(lat_y):
        lat, y = lat_y
        return np.array([HIP_MID[0] + racket_dir * lat * TORSO_LEN, y])

    rest = to_px((0.3, 520.0))
    off_rest = np.array([HIP_MID[0] - racket_dir * 0.3 * TORSO_LEN, 520.0])

    # per-stroke kinds can differ, so build the racket path stroke by stroke
    racket = np.tile(rest, (n_frames, 1)).astype(float)
    for c, kind in strokes:
        shape = STROKE_SHAPES[kind]
        tb, pc = to_px(shape["takeback"]), to_px(shape["contact"])
        p_end = 2 * pc - tb
        racket += np.outer(_ramp(t, c - 28, 11.0), tb - rest)
        racket += np.outer(_ramp(t, c, 2.5), p_end - tb)
        racket += np.outer(_ramp(t, c + 25, 12.0), rest - p_end)

    off_path = np.tile(off_rest, (n_frames, 1)).astype(float)
    if off_wrist_faster:
        for c, kind in strokes:
            shape = STROKE_SHAPES[kind]
            # off wrist: one fast sweep (peak at contact, ~1.5x the racket
            # wrist's amplitude) with no separate takeback, so only the true
            # contact peak is created -- but it is the FASTER wrist.
            d = (to_px(shape["contact"]) - to_px(shape["takeback"])) * 2 * -1.5
            off_path += np.outer(_ramp(t, c, 2.5), d)
            off_path += np.outer(_ramp(t, c + 25, 12.0), -d)

    lm = np.zeros((n_frames, NUM_LANDMARKS, 2))
    lm[:] = HIP_MID
    sm = (HIP_MID[0], HIP_MID[1] - TORSO_LEN)
    lm[:, R_SHOULDER] = (sm[0] + right_dir * 50, sm[1])
    lm[:, L_SHOULDER] = (sm[0] - right_dir * 50, sm[1])
    lm[:, R_HIP] = (HIP_MID[0] + right_dir * 30, HIP_MID[1])
    lm[:, L_HIP] = (HIP_MID[0] - right_dir * 30, HIP_MID[1])
    lm[:, NOSE] = (sm[0], sm[1] - 50)
    r_idx, o_idx = (R_WRIST, L_WRIST) if hand == "right" else (L_WRIST, R_WRIST)
    lm[:, r_idx] = racket
    lm[:, o_idx] = off_path

    return PlayerLandmarkSequence(
        landmarks=lm, visibility=np.ones((n_frames, NUM_LANDMARKS)),
        detected=np.ones(n_frames, dtype=bool), fps=fps, width=1280, height=720,
    )
