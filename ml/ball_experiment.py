"""EXPERIMENTS behind ml/ball_filter.py: ball-blob presence and ball-trajectory
reversal around a detected event.

Question asked: can a crude colour cue separate detections that are ball
contacts from detections that are not? Parameters were fixed before the first
run and not tuned afterwards. See docs/real-footage-findings.md for results and
caveats (19 labeled detections, 2 clips, colour cue only).

    python -m ml.ball_experiment LABEL.json [...] --videos-dir data/videos
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np

from ml.ball_filter import (AREA_MAX, AREA_MIN, HSV_HI, HSV_LO,
                            ball_frames_near_wrists)

# Trajectory variant (second experiment): fixed before its single run.
TRAJ_WINDOW = 12             # frames either side of the event
TRAJ_RADIUS_TORSO = 4.0      # wider than WINDOW's search: the ball travels before/after contact
MIN_SIDE = 2                 # ball points needed on each side of the reversal
MIN_SPEED = 2.0              # px/frame of horizontal speed on each side


def nearest_ball_x(frame_bgr: np.ndarray, wrists: Sequence[Sequence[float]],
                   radius_px: float) -> Optional[float]:
    """Horizontal position of the ball-coloured blob nearest a wrist, within radius."""
    mask = cv2.inRange(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV), HSV_LO, HSV_HI)
    _, _, stats, centres = cv2.connectedComponentsWithStats(mask)
    best = None
    for s, c in zip(stats[1:], centres[1:]):
        if not AREA_MIN <= s[4] <= AREA_MAX:
            continue
        d = min((np.linalg.norm(c - np.asarray(w, float)) for w in wrists
                 if not np.isnan(np.asarray(w, float)).any()), default=np.inf)
        if d <= radius_px and (best is None or d < best[0]):
            best = (d, float(c[0]))
    return None if best is None else best[1]


def horizontal_reversal(track: Sequence[tuple[int, float]],
                        min_side: int = MIN_SIDE, min_speed: float = MIN_SPEED) -> Optional[bool]:
    """Does the ball's horizontal motion reverse? None when there are too few points.

    From a side-on camera a struck ball comes toward the player and leaves the
    other way; a ball that only passes by keeps its direction. ``track`` is
    [(frame, x)] sorted by frame.
    """
    if len(track) < 2 * min_side:
        return None

    def median_speed(pts):
        if len(pts) < 2:
            return 0.0
        return float(np.median([(x2 - x1) / (f2 - f1) for (f1, x1), (f2, x2) in zip(pts, pts[1:])]))

    for k in range(min_side, len(track) - min_side + 1):
        va, vb = median_speed(track[:k]), median_speed(track[k:])
        if abs(va) >= min_speed and abs(vb) >= min_speed and np.sign(va) != np.sign(vb):
            return True
    return False


def main(argv: Optional[list[str]] = None) -> int:
    from cv.pose_overlay import extract_player_landmarks
    from ml.eval_shot_timing import load_label, match_one_to_one
    from ml.shot_timing import detect_shots

    ap = argparse.ArgumentParser(prog="python -m ml.ball_experiment")
    ap.add_argument("labels", nargs="+", type=Path)
    ap.add_argument("--videos-dir", type=Path, required=True)
    args = ap.parse_args(argv)

    out = []
    for lab_path in args.labels:
        label = load_label(lab_path)
        video = next(iter(sorted(args.videos_dir.glob(label["video_id"] + ".*"))))
        seq = extract_player_landmarks(video, progress=False)
        det = [e.contact_frame for e in detect_shots(seq).events]
        true = {d for _, d in match_one_to_one(det, label["contact_frames"], 10)["tp"]}
        for d in det:
            out.append({"video_id": label["video_id"], "frame": d, "matches_owner_contact": d in true,
                        "frames_with_ball_blob": ball_frames_near_wrists(video, seq.landmarks, d)})
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
