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
from typing import Callable, Optional

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


# ------------------------------------------------------------------- shared
# Both the video-rendering path (generate_pose_overlay) and the
# landmarks-only path (extract_player_landmarks, added for M2 shot-timing
# work -- see ml/shot_timing.py) need the same "decode frame -> run
# MediaPipe -> build candidates -> pick the target player" sequence. This
# helper holds that shared core once, so the two paths cannot drift apart;
# each path supplies its own ``on_frame`` callback for what it does with the
# result (draw + write a video frame, vs. just record the landmarks).
def _run_pose_pipeline(
    video_path: Path,
    *,
    model_complexity: int,
    num_candidate_poses: int,
    player_selector: Optional[PlayerSelector],
    min_pose_detection_confidence: float,
    max_frames: Optional[int],
    progress: bool,
    log_label: str,
    on_frame: Callable[[int, np.ndarray, list[PersonCandidate], Optional[int]], None],
) -> tuple[int, float, int, int, int]:
    """Decode ``video_path``, run pose detection + player selection per frame.

    Calls ``on_frame(frame_index, frame, candidates, chosen_idx)`` for every
    decoded frame (``chosen_idx`` is None if no candidate was selected).

    Returns:
        (frame_count, fps, width, height, frames_with_pose).

    Raises:
        FileNotFoundError: video_path does not exist.
        RuntimeError: the video can't be decoded or has no frames.
    """
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision

    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    selector = player_selector or default_player_selector()
    selector.reset()

    model_path = ensure_model(model_complexity, progress=progress)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video (unsupported codec?): {video_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

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
                    frames_with_pose += 1

                on_frame(frame_count, frame, candidates, chosen_idx)

                frame_count += 1
                if progress and frame_count % 30 == 0:
                    print(f"\r[{log_label}] processed {frame_count} frames...",
                          end="", flush=True)
    finally:
        cap.release()

    if progress:
        print(f"\r[{log_label}] done, {frame_count} frames "
              f"({frames_with_pose} with a detected player)          ")

    if frame_count == 0:
        raise RuntimeError(f"Video had no readable frames: {video_path}")

    return frame_count, fps, width, height, frames_with_pose


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
    video_path = Path(video_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Writer is created lazily inside the callback once we know fps/width/
    # height from the first call into the shared pipeline -- but those are
    # only available *after* _run_pose_pipeline opens the capture. Open our
    # own capture just for those dimensions would duplicate work, so instead
    # we open the writer up front using the same probe cv2.VideoCapture does
    # internally: cheapest is to peek the video once here.
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    probe = cv2.VideoCapture(str(video_path))
    if not probe.isOpened():
        probe.release()
        raise RuntimeError(f"Could not open video (unsupported codec?): {video_path}")
    fps = float(probe.get(cv2.CAP_PROP_FPS)) or 30.0
    width = int(probe.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(probe.get(cv2.CAP_PROP_FRAME_HEIGHT))
    probe.release()

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"Could not open output video for writing: {output_path}")

    def on_frame(_frame_idx, frame, candidates, chosen_idx):
        if chosen_idx is not None:
            _draw_skeleton(frame, candidates[chosen_idx])
        writer.write(frame)

    try:
        frame_count, fps, width, height, frames_with_pose = _run_pose_pipeline(
            video_path,
            model_complexity=model_complexity,
            num_candidate_poses=num_candidate_poses,
            player_selector=player_selector,
            min_pose_detection_confidence=min_pose_detection_confidence,
            max_frames=max_frames,
            progress=progress,
            log_label="pose_overlay",
            on_frame=on_frame,
        )
    finally:
        writer.release()

    return PoseOverlayResult(
        output_path=output_path,
        frame_count=frame_count,
        fps=fps,
        width=width,
        height=height,
        frames_with_pose=frames_with_pose,
    )


# ------------------------------------------------------ landmarks-only path
@dataclasses.dataclass
class PlayerLandmarkSequence:
    """Per-frame landmark time series for the selected target player.

    Built for M2 shot-timing work (ml/shot_timing.py), which needs the raw
    wrist trajectory over time -- something ``generate_pose_overlay`` does
    not expose since it only draws into a video and returns summary stats.

    Attributes:
        landmarks: (frame_count, NUM_LANDMARKS, 2) pixel-space (x, y)
            coordinates. A frame with no selected player is all-NaN.
        visibility: (frame_count, NUM_LANDMARKS) per-landmark visibility/
            confidence in [0, 1]; all-zero on a frame with no selected
            player.
        detected: (frame_count,) bool -- whether a player was selected that
            frame (mirrors ``PoseOverlayResult.pose_detection_rate`` but
            per-frame instead of a single fraction).
        fps: source video frame rate.
        width: source video frame width in pixels.
        height: source video frame height in pixels.
    """

    landmarks: np.ndarray
    visibility: np.ndarray
    detected: np.ndarray
    fps: float
    width: int
    height: int

    @property
    def frame_count(self) -> int:
        return int(self.detected.shape[0])

    @property
    def pose_detection_rate(self) -> float:
        return float(self.detected.mean()) if self.frame_count else 0.0


def extract_player_landmarks(
    video_path: str | Path,
    *,
    model_complexity: int = 0,
    num_candidate_poses: int = 3,
    player_selector: Optional[PlayerSelector] = None,
    min_pose_detection_confidence: float = 0.5,
    max_frames: Optional[int] = None,
    progress: bool = True,
) -> PlayerLandmarkSequence:
    """Extract the selected target player's per-frame landmark time series.

    This reuses the same model-loading, decoding, and player-selection
    internals as ``generate_pose_overlay`` (via ``_run_pose_pipeline``) but
    does not draw or write an output video -- it just records landmark
    coordinates, meant for downstream signal processing (e.g. M2's
    wrist-speed swing-timing detector in ml/shot_timing.py) rather than
    for a human to watch.

    Args / Returns / Raises: same as ``generate_pose_overlay`` minus
    ``output_path``; see that function's docstring.
    """
    video_path = Path(video_path)

    landmarks: list[np.ndarray] = []
    visibility: list[np.ndarray] = []
    detected: list[bool] = []

    def on_frame(_frame_idx, _frame, candidates, chosen_idx):
        if chosen_idx is not None:
            cand = candidates[chosen_idx]
            pts = cand.landmarks
            if pts.shape[0] < NUM_LANDMARKS:
                padded = np.full((NUM_LANDMARKS, 2), np.nan)
                padded[:pts.shape[0]] = pts
                pts = padded
            landmarks.append(pts.copy())
            visibility.append(np.full(NUM_LANDMARKS, cand.mean_visibility))
            detected.append(True)
        else:
            landmarks.append(np.full((NUM_LANDMARKS, 2), np.nan))
            visibility.append(np.zeros(NUM_LANDMARKS))
            detected.append(False)

    frame_count, fps, width, height, _frames_with_pose = _run_pose_pipeline(
        video_path,
        model_complexity=model_complexity,
        num_candidate_poses=num_candidate_poses,
        player_selector=player_selector,
        min_pose_detection_confidence=min_pose_detection_confidence,
        max_frames=max_frames,
        progress=progress,
        log_label="extract_player_landmarks",
        on_frame=on_frame,
    )

    return PlayerLandmarkSequence(
        landmarks=np.stack(landmarks, axis=0),
        visibility=np.stack(visibility, axis=0),
        detected=np.array(detected, dtype=bool),
        fps=fps,
        width=width,
        height=height,
    )
