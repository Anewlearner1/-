"""EXPERIMENT (not wired into the pipeline): is there a ball-coloured blob near
a wrist around a detected event?

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

HSV_LO = (25, 90, 150)      # tennis-ball yellow-green: hue, saturation, value
HSV_HI = (50, 255, 255)
AREA_MIN, AREA_MAX = 3, 200  # px at the source resolution (clips are 360-854 wide)
RADIUS_TORSO = 2.0           # blob must lie within this many torso lengths of a wrist
WINDOW = 8                   # frames either side of the detected event


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


def frames_with_ball(video: Path, landmarks: np.ndarray, event_frame: int) -> int:
    """How many of the 2*WINDOW+1 frames around the event show a ball blob near a wrist."""
    shoulders = (landmarks[:, 11] + landmarks[:, 12]) / 2
    hips = (landmarks[:, 23] + landmarks[:, 24]) / 2
    torso = np.linalg.norm(shoulders - hips, axis=1)
    cap = cv2.VideoCapture(str(video))
    hits = 0
    try:
        for f in range(max(0, event_frame - WINDOW), min(len(landmarks), event_frame + WINDOW + 1)):
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, im = cap.read()
            if ok and find_ball_blob(im, [landmarks[f, 15], landmarks[f, 16]],
                                     RADIUS_TORSO * torso[f]) is not None:
                hits += 1
    finally:
        cap.release()
    return hits


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
                        "frames_with_ball_blob": frames_with_ball(video, seq.landmarks, d)})
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
