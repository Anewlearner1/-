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
