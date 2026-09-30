"""Helpers to write small synthetic mp4 files for tests.

Pattern adapted from the sibling tennis-form-coach project's
tests/test_cli.py (synthetic-video-via-cv2.VideoWriter approach only --
no domain code shared between the two projects).
"""

from pathlib import Path

import numpy as np
import cv2


def write_static_video(path: "str | Path", fps: float, n_frames: int,
                        width: int = 320, height: int = 240,
                        color: int = 18) -> Path:
    """Writes a solid-color (i.e. perfectly static / zero camera motion)
    video. Useful for a "camera is fixed" positive case and for fps checks.
    """
    path = Path(path)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                              fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError("mp4 encoder unavailable in this environment")
    frame = np.full((height, width, 3), color, np.uint8)
    for _ in range(n_frames):
        writer.write(frame)
    writer.release()
    return path


def write_jittery_video(path: "str | Path", fps: float, n_frames: int,
                         width: int = 320, height: int = 240,
                         max_shift: int = 25, seed: int = 0) -> Path:
    """Writes a video of a textured pattern that jumps around randomly
    frame to frame, simulating handheld camera shake.
    """
    rng = np.random.default_rng(seed)
    path = Path(path)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                              fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError("mp4 encoder unavailable in this environment")

    # Build a larger textured canvas so we can crop a shifted window each
    # frame to simulate camera translation.
    pad = max_shift * 2
    canvas = (rng.integers(0, 255, (height + 2 * pad, width + 2 * pad, 3))
              .astype(np.uint8))
    # Add some coarse structure (blocks) so phase correlation has features
    # to lock onto, rather than pure noise.
    for _ in range(40):
        y0 = rng.integers(0, height + 2 * pad - 20)
        x0 = rng.integers(0, width + 2 * pad - 20)
        canvas[y0:y0 + 20, x0:x0 + 20] = rng.integers(0, 255, 3)

    for _ in range(n_frames):
        dy = int(rng.integers(-max_shift, max_shift + 1))
        dx = int(rng.integers(-max_shift, max_shift + 1))
        y0, x0 = pad + dy, pad + dx
        frame = canvas[y0:y0 + height, x0:x0 + width]
        writer.write(frame)
    writer.release()
    return path


def write_steady_textured_video(path: "str | Path", fps: float, n_frames: int,
                                 width: int = 320, height: int = 240,
                                 seed: int = 0) -> Path:
    """Writes a textured but motionless video (fixed camera, non-blank
    frames) -- a more realistic "tripod" positive case than a flat color.
    """
    rng = np.random.default_rng(seed)
    path = Path(path)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                              fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError("mp4 encoder unavailable in this environment")
    frame = rng.integers(0, 255, (height, width, 3)).astype(np.uint8)
    for _ in range(n_frames):
        writer.write(frame)
    writer.release()
    return path
