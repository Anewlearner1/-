"""Pure geometric / signal helpers.

Everything here is plain NumPy with no dependency on MediaPipe or OpenCV, which
keeps it trivially unit-testable against hand-computed values.

Coordinate convention (matches MediaPipe *world* landmarks):
    x -> subject's right is positive
    y -> DOWN is positive
    z -> toward the camera is negative
Origin is roughly the mid-hip point, units are metres.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import savgol_filter

EPS = 1e-9


# --------------------------------------------------------------------------
# vectors & angles
# --------------------------------------------------------------------------
def unit(v: np.ndarray) -> np.ndarray:
    """Normalise vectors along the last axis; zero-length vectors stay zero."""
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n < EPS, 1.0, n)


def angle_3p(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Interior angle at vertex ``b`` of the path a-b-c, in degrees [0, 180].

    Accepts single points of shape (D,) or stacks of shape (..., D).
    """
    a, b, c = np.asarray(a, float), np.asarray(b, float), np.asarray(c, float)
    cos = np.sum(unit(a - b) * unit(c - b), axis=-1)
    return np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))


def angle_between(v1: np.ndarray, v2: np.ndarray) -> np.ndarray:
    """Unsigned angle between two vectors, in degrees [0, 180]."""
    cos = np.sum(unit(np.asarray(v1, float)) * unit(np.asarray(v2, float)), axis=-1)
    return np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))


def signed_angle_2d(v1: np.ndarray, v2: np.ndarray) -> np.ndarray:
    """Signed angle from ``v1`` to ``v2`` in a 2D plane, degrees (-180, 180].

    Positive is counter-clockwise in a standard right-handed 2D frame.
    """
    v1, v2 = np.asarray(v1, float), np.asarray(v2, float)
    dot = v1[..., 0] * v2[..., 0] + v1[..., 1] * v2[..., 1]
    cross = v1[..., 0] * v2[..., 1] - v1[..., 1] * v2[..., 0]
    return np.degrees(np.arctan2(cross, dot))


def ground_yaw(p_from: np.ndarray, p_to: np.ndarray) -> np.ndarray:
    """Compass-style yaw of the segment p_from -> p_to projected on the ground.

    Uses the (x, z) ground plane and ignores height. Returned in degrees
    (-180, 180]. This is what the shoulder-line and hip-line rotations are
    measured with, so their difference gives the hip-shoulder separation.
    """
    p_from, p_to = np.asarray(p_from, float), np.asarray(p_to, float)
    d = p_to - p_from
    return np.degrees(np.arctan2(d[..., 2], d[..., 0]))


def wrap_deg(a: np.ndarray) -> np.ndarray:
    """Wrap angles into [-180, 180)."""
    return (np.asarray(a, float) + 180.0) % 360.0 - 180.0


def unwrap_deg(a: np.ndarray) -> np.ndarray:
    """Remove 360-degree jumps from an angle time series."""
    return np.degrees(np.unwrap(np.radians(np.asarray(a, float))))


# --------------------------------------------------------------------------
# time series
# --------------------------------------------------------------------------
def derivative(series: np.ndarray, fps: float) -> np.ndarray:
    """Per-second time derivative along axis 0, same length as the input."""
    series = np.asarray(series, float)
    if series.shape[0] < 2:
        return np.zeros_like(series)
    return np.gradient(series, 1.0 / float(fps), axis=0)


def speed(points: np.ndarray, fps: float) -> np.ndarray:
    """Scalar speed (m/s) of a (T, D) point trajectory."""
    return np.linalg.norm(derivative(np.asarray(points, float), fps), axis=-1)


def smooth(series: np.ndarray, window: int = 7, poly: int = 2) -> np.ndarray:
    """Savitzky-Golay smoothing along axis 0, degrading gracefully when short.

    Window is forced odd and clipped to the series length; if the series is too
    short for any meaningful filter it is returned untouched.
    """
    series = np.asarray(series, float)
    n = series.shape[0]
    w = min(int(window), n if n % 2 == 1 else n - 1)
    if w % 2 == 0:
        w -= 1
    if n < 5 or w < 5 or w <= poly:
        return series
    return savgol_filter(series, w, poly, axis=0)


def interpolate_gaps(coords: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Linearly interpolate frames flagged invalid, per coordinate channel.

    ``coords`` is (T, ...) and ``valid`` is a boolean (T,) mask. Leading and
    trailing invalid runs are filled with the nearest valid value. If nothing
    is valid the input is returned unchanged.
    """
    coords = np.array(coords, float, copy=True)
    valid = np.asarray(valid, bool)
    if valid.all() or not valid.any():
        return coords

    t = np.arange(coords.shape[0], dtype=float)
    flat = coords.reshape(coords.shape[0], -1)
    for ch in range(flat.shape[1]):
        flat[:, ch] = np.interp(t, t[valid], flat[valid, ch])
    return flat.reshape(coords.shape)


def longest_invalid_run(valid: np.ndarray) -> int:
    """Length of the longest consecutive run of invalid frames."""
    valid = np.asarray(valid, bool)
    best = run = 0
    for v in valid:
        run = 0 if v else run + 1
        best = max(best, run)
    return best


# --------------------------------------------------------------------------
# scale normalisation
# --------------------------------------------------------------------------
def body_scale(shoulder_l: np.ndarray, shoulder_r: np.ndarray,
               hip_l: np.ndarray, hip_r: np.ndarray) -> float:
    """A robust per-subject length unit: mean shoulder-to-hip torso length.

    Distances expressed as multiples of this make metrics comparable across
    players of different heights and across different camera distances.
    """
    shoulder_mid = (np.asarray(shoulder_l, float) + np.asarray(shoulder_r, float)) / 2.0
    hip_mid = (np.asarray(hip_l, float) + np.asarray(hip_r, float)) / 2.0
    torso = np.linalg.norm(shoulder_mid - hip_mid, axis=-1)
    scale = float(np.median(np.atleast_1d(torso)))
    return scale if scale > EPS else 1.0
