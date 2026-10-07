"""Optional filter: keep a detected shot only if a ball is seen near the player.

Pose alone fires on every fast arm motion, so about half its detections are
not hits (ready-stance movement, edit cuts, follow-through). A hit needs a ball
at the racket, so this keeps an event only when a tennis-ball-coloured blob
appears within ``RADIUS_TORSO`` torso lengths of either wrist in at least
``min_ball_frames`` of the frames within +/-``window`` of it.

Measured on three short side-view 30 fps practice clips with owner labels
(docs/real-footage-findings.md): precision 0.52 -> 0.95, recall 1.00 -> 0.82;
on the one clip not used to build it, 0.57 -> 0.91 and 1.00 -> 0.77. Misses
are hits whose ball the colour threshold did not pick up. Untested on the
behind-the-baseline target setup (small ball) and on rallies where a ball
passes a player who does not hit it. Off by default for those reasons.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np

HSV_LO = (25, 90, 150)      # tennis-ball yellow-green: hue, saturation, value
HSV_HI = (50, 255, 255)
AREA_MIN, AREA_MAX = 3, 200  # px at the source resolution (clips were 360-854 wide)
RADIUS_TORSO = 2.0           # blob must lie within this many torso lengths of a wrist
WINDOW = 8                   # frames either side of the detected event (30 fps)
# filter_shots_with_ball scales the window by fps so 60 fps clips search the
# same +/-0.27 s; at 29.97/30 fps this is exactly the 8 frames that were measured.
WINDOW_S = WINDOW / 30.0


def find_ball_blob(frame_bgr: np.ndarray, wrists: Sequence[Sequence[float]],
                   radius_px: float) -> Optional[int]:
    """Area of the first ball-coloured blob within ``radius_px`` of a wrist, else None."""
    mask = cv2.inRange(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV), HSV_LO, HSV_HI)
    _, _, stats, centres = cv2.connectedComponentsWithStats(mask)
    for s, c in zip(stats[1:], centres[1:]):
        if not AREA_MIN <= s[4] <= AREA_MAX:
            continue
        for w in wrists:
            w = np.asarray(w, float)
            if not np.isnan(w).any() and np.linalg.norm(c - w) <= radius_px:
                return int(s[4])
    return None


def ball_frames_near_wrists(video: Path, landmarks: np.ndarray, event_frame: int,
                            window: int = WINDOW) -> int:
    """How many frames within +/-``window`` of the event show a ball blob near a wrist."""
    shoulders = (landmarks[:, 11] + landmarks[:, 12]) / 2
    hips = (landmarks[:, 23] + landmarks[:, 24]) / 2
    torso = np.linalg.norm(shoulders - hips, axis=1)
    cap = cv2.VideoCapture(str(video))
    hits = 0
    try:
        for f in range(max(0, event_frame - window), min(len(landmarks), event_frame + window + 1)):
            if np.isnan(torso[f]):
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, im = cap.read()
            if ok and find_ball_blob(im, [landmarks[f, 15], landmarks[f, 16]],
                                     RADIUS_TORSO * torso[f]) is not None:
                hits += 1
    finally:
        cap.release()
    return hits


def filter_shots_with_ball(video: Path, seq, result, *, min_ball_frames: int = 1,
                           window: Optional[int] = None):
    """Return ``(filtered_result, counts)``; ``counts`` maps event frame -> ball frames.

    ``seq`` is the ``PlayerLandmarkSequence`` the events were detected on and
    ``result`` the ``ShotTimingResult``; the returned result keeps its speed
    trace and drops the events without enough ball frames.
    """
    if min_ball_frames < 1:
        raise ValueError("min_ball_frames must be at least 1")
    if window is None:
        window = max(1, int(round(WINDOW_S * seq.fps)))
    counts = {e.contact_frame: ball_frames_near_wrists(Path(video), seq.landmarks,
                                                       e.contact_frame, window)
              for e in result.events}
    kept = [e for e in result.events if counts[e.contact_frame] >= min_ball_frames]
    return dataclasses.replace(result, events=kept), counts
