"""M1 deliverable: player selection + MediaPipe pose -> skeleton-overlay video.

Pipeline for this milestone:

    frame --> MediaPipe PoseLandmarker (multi-person, VIDEO mode)
          --> per-frame PersonCandidate list (cv.player_selection)
          --> PlayerSelector picks the target player's index (swappable stage)
          --> skeleton for that candidate only is drawn on the frame
          --> frame written to the output video

Design notes
------------
The technical plan's suggestion is "RTMPose offline / MediaPipe on-device",
noting MediaPipe expects a single person so you'd normally crop to the
player region first. This module instead runs MediaPipe directly on the
full frame with ``num_poses > 1`` (MediaPipe's Tasks API does support
detecting several people at once; it is not limited to exactly one), and
uses the ``player_selection`` stage to pick which of the detected people to
draw. That sidesteps needing a separate crop/detector step for this first
pass while still producing the real, swappable "pick the target player"
architecture the plan calls for -- see cv/player_selection.py.

This mirrors the MediaPipe usage pattern in the sibling tennis-form-coach
project (tennis_coach/pose.py: Tasks API, VIDEO running mode, on-first-use
model download/caching) but is a separate implementation -- Rally AI's real
input may have multiple people in frame (opponent, ball kids), which
tennis-form-coach's single-player assumption does not need to handle.

Known limitation / not yet validated: MediaPipe's shipped pose model was
trained on general fitness/human-pose datasets, not phone-shot tennis
footage specifically. Detection worked on a real test photo during
development (see tests/test_pose_overlay.py), but accuracy on fast-motion
tennis strokes (motion blur, extended limbs, side-on views) has not been
validated against ground truth and should not be assumed to match any
published benchmark for this model -- see docs/technical-plan.md §7.
"""
from __future__ import annotations

import dataclasses
import os
import urllib.request
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .player_selection import PersonCandidate, PlayerSelector, default_player_selector

# --------------------------------------------------------------- landmarks
# MediaPipe Pose's 33-point layout (BlazePose topology). Duplicated here
# (rather than imported from tennis-form-coach) because this is meant to be
# a standalone module in a separate codebase -- see module docstring.
NOSE = 0
L_SHOULDER, R_SHOULDER = 11, 12
L_ELBOW, R_ELBOW = 13, 14
L_WRIST, R_WRIST = 15, 16
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28
L_FOOT, R_FOOT = 31, 32

NUM_LANDMARKS = 33

SKELETON = [
    (L_SHOULDER, R_SHOULDER), (L_HIP, R_HIP),
    (L_SHOULDER, L_HIP), (R_SHOULDER, R_HIP),
    (L_SHOULDER, L_ELBOW), (L_ELBOW, L_WRIST),
    (R_SHOULDER, R_ELBOW), (R_ELBOW, R_WRIST),
    (L_HIP, L_KNEE), (L_KNEE, L_ANKLE), (L_ANKLE, L_FOOT),
    (R_HIP, R_KNEE), (R_KNEE, R_ANKLE), (R_ANKLE, R_FOOT),
    (NOSE, L_SHOULDER), (NOSE, R_SHOULDER),
]

MIN_VISIBILITY = 0.5

POINT_COLOR = (0, 255, 0)      # BGR
LINE_COLOR = (0, 200, 255)
POINT_RADIUS = 4
LINE_THICKNESS = 2

# ------------------------------------------------------------------- model
# Same models/hosting as tennis-form-coach (Apache 2.0 licensed -- see
# docs/technical-plan.md §4). Re-declared here rather than imported so this
# module has no cross-repo dependency.
MODEL_URLS = {
    0: ("pose_landmarker_lite.task",
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
        "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"),
    1: ("pose_landmarker_full.task",
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
        "pose_landmarker_full/float16/latest/pose_landmarker_full.task"),
    2: ("pose_landmarker_heavy.task",
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
        "pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task"),
}


def cache_dir() -> Path:
    """Where downloaded pose models live (override with RALLY_AI_CACHE)."""
    root = os.environ.get("RALLY_AI_CACHE")
    path = Path(root) if root else Path.home() / ".cache" / "rally-ai"
    path = path / "pose"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_model(model_complexity: int = 0, progress: bool = True) -> Path:
    """Path to the pose model, downloading it on first use.

    Raises:
        RuntimeError: the model is absent and cannot be downloaded.
    """
    name, url = MODEL_URLS.get(model_complexity, MODEL_URLS[0])
    path = cache_dir() / name
    if path.exists() and path.stat().st_size > 0:
        return path

    if progress:
        print(f"[pose_overlay] downloading pose model {name} ...")
    tmp = path.with_suffix(".part")
    try:
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(path)
    except Exception as e:  # network, proxy, disk...
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"Could not download pose model ({e}). Download it manually to "
            f"{path}:\n  {url}") from e
    return path


# ----------------------------------------------------------------- result
@dataclasses.dataclass
class PoseOverlayResult:
    """Summary of a pose-overlay render."""

    output_path: Path
    frame_count: int
    fps: float
    width: int
    height: int
    frames_with_pose: int

    @property
    def pose_detection_rate(self) -> float:
        """Fraction of frames where the selected player's pose was drawn."""
        return self.frames_with_pose / self.frame_count if self.frame_count else 0.0


# --------------------------------------------------------------- candidates
def _candidates_from_result(pose_landmarks_list, width: int, height: int
                             ) -> list[PersonCandidate]:
    """Build PersonCandidate objects from one frame's MediaPipe result."""
    candidates = []
    for landmarks in pose_landmarks_list:
        pts = np.array([[p.x * width, p.y * height] for p in landmarks], float)
        vis = np.array([getattr(p, "visibility", 1.0) for p in landmarks], float)
        x_min, y_min = pts.min(axis=0)
        x_max, y_max = pts.max(axis=0)
        candidates.append(PersonCandidate(
            bbox=(float(x_min), float(y_min), float(x_max), float(y_max)),
            landmarks=pts,
            mean_visibility=float(vis.mean()) if vis.size else 0.0,
        ))
    return candidates


def _draw_skeleton(frame: np.ndarray, candidate: PersonCandidate) -> None:
    """Draw the skeleton for one selected candidate directly onto ``frame``."""
    pts = candidate.landmarks
    if pts.shape[0] < NUM_LANDMARKS:
        return

    for a, b in SKELETON:
        pa, pb = pts[a], pts[b]
        if np.any(np.isnan(pa)) or np.any(np.isnan(pb)):
            continue
        cv2.line(frame, (int(pa[0]), int(pa[1])), (int(pb[0]), int(pb[1])),
                  LINE_COLOR, LINE_THICKNESS, lineType=cv2.LINE_AA)

    for idx in range(NUM_LANDMARKS):
        p = pts[idx]
        if np.any(np.isnan(p)):
            continue
        cv2.circle(frame, (int(p[0]), int(p[1])), POINT_RADIUS, POINT_COLOR, -1,
                    lineType=cv2.LINE_AA)


# ------------------------------------------------------------------- main
def generate_pose_overlay(
    video_path: str | Path,
    output_path: str | Path,
    *,
    model_complexity: int = 0,
    num_candidate_poses: int = 3,
    player_selector: Optional[PlayerSelector] = None,
    min_pose_detection_confidence: float = 0.5,
    max_frames: Optional[int] = None,
    progress: bool = True,
) -> PoseOverlayResult:
    """Render a skeleton-overlay video for the target player in ``video_path``.

    Args:
        video_path: source video to read.
        output_path: where to write the overlay video (mp4).
        model_complexity: 0=lite, 1=full, 2=heavy (MediaPipe PoseLandmarker
            model variants; higher = more accurate, slower).
        num_candidate_poses: how many people MediaPipe should detect per
            frame before player_selection picks one. Set to 1 to reproduce
            tennis-form-coach's single-person assumption; the Rally AI
            default is >1 because real clips may have an opponent/ball
            kids in frame.
        player_selector: the swappable "pick the target player" stage (see
            cv/player_selection.py). Defaults to the M1 stub.
        max_frames: optional cap, mainly for fast tests.
        progress: print a one-line progress ticker.

    Returns:
        PoseOverlayResult summarizing the render, including what fraction
        of frames actually had a pose drawn.

    Raises:
        FileNotFoundError: video_path does not exist.
        RuntimeError: the video can't be decoded, has no frames, or the
            output video file could not be opened for writing.
    """
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision

    video_path = Path(video_path)
    output_path = Path(output_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    selector = player_selector or default_player_selector()
    selector.reset()

    model_path = ensure_model(model_complexity, progress=progress)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video (unsupported codec?): {video_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError(f"Could not open output video for writing: {output_path}")

    options = mp_vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
        running_mode=mp_vision.RunningMode.VIDEO,
        num_poses=max(1, num_candidate_poses),
        min_pose_detection_confidence=min_pose_detection_confidence,
        min_pose_presence_confidence=min_pose_detection_confidence,
        min_tracking_confidence=min_pose_detection_confidence,
    )

    frame_count = 0
    frames_with_pose = 0

    try:
        with mp_vision.PoseLandmarker.create_from_options(options) as landmarker:
            while True:
                if max_frames is not None and frame_count >= max_frames:
                    break
                ok, frame = cap.read()
                if not ok:
                    break

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                timestamp_ms = int(frame_count * 1000 / fps)
                result = landmarker.detect_for_video(mp_image, timestamp_ms)

                candidates = _candidates_from_result(
                    result.pose_landmarks or [], width, height)
                chosen_idx = selector.select(candidates)

                if chosen_idx is not None:
                    _draw_skeleton(frame, candidates[chosen_idx])
                    frames_with_pose += 1

                writer.write(frame)
                frame_count += 1
                if progress and frame_count % 30 == 0:
                    print(f"\r[pose_overlay] processed {frame_count} frames...",
                          end="", flush=True)
    finally:
        cap.release()
        writer.release()

    if progress:
        print(f"\r[pose_overlay] done, {frame_count} frames "
              f"({frames_with_pose} with a detected player)          ")

    if frame_count == 0:
        raise RuntimeError(f"Video had no readable frames: {video_path}")

    return PoseOverlayResult(
        output_path=output_path,
        frame_count=frame_count,
        fps=fps,
        width=width,
        height=height,
        frames_with_pose=frames_with_pose,
    )
