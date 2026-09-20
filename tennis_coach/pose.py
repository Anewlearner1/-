"""MediaPipe pose estimation, wrapped into one smoothed, gap-filled sequence.

MediaPipe 0.10.30 removed the old `mediapipe.solutions.pose` API, so this uses
the Tasks API (`PoseLandmarker`) and downloads the model on first use into a
local cache rather than expecting it to be installed alongside the package.

Two coordinate sets come out of the tracker and both are kept:
  * `world`  — metric-ish 3D landmarks, hip-centred. Everything angular and
    every real-world length is measured from these.
  * `pixel`  — image-space 2D landmarks. Used for drawing, for cropping, and
    for anything that has to line up with the raw video frames.
"""
from __future__ import annotations

import dataclasses
import os
import urllib.request
from pathlib import Path

import numpy as np

from . import geometry as geo

# ---------------------------------------------------------------- landmarks
NOSE = 0
L_EAR, R_EAR = 7, 8
L_SHOULDER, R_SHOULDER = 11, 12
L_ELBOW, R_ELBOW = 13, 14
L_WRIST, R_WRIST = 15, 16
L_PINKY, R_PINKY = 17, 18
L_INDEX, R_INDEX = 19, 20
L_THUMB, R_THUMB = 21, 22
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28
L_HEEL, R_HEEL = 29, 30
L_FOOT, R_FOOT = 31, 32

NUM_LANDMARKS = 33

#: Below this tracker confidence a landmark is treated as missing: interpolated
#: for measurement, skipped for drawing.
MIN_VISIBILITY = 0.5

SKELETON = [
    (L_SHOULDER, R_SHOULDER), (L_HIP, R_HIP),
    (L_SHOULDER, L_HIP), (R_SHOULDER, R_HIP),
    (L_SHOULDER, L_ELBOW), (L_ELBOW, L_WRIST),
    (R_SHOULDER, R_ELBOW), (R_ELBOW, R_WRIST),
    (L_HIP, L_KNEE), (L_KNEE, L_ANKLE), (L_ANKLE, L_FOOT),
    (R_HIP, R_KNEE), (R_KNEE, R_ANKLE), (R_ANKLE, R_FOOT),
    (NOSE, L_SHOULDER), (NOSE, R_SHOULDER),
]

#: The landmarks every measurement depends on. Tracking quality is judged on
#: these alone -- a lost fingertip does not matter, a lost hip does.
CRITICAL = [L_SHOULDER, R_SHOULDER, L_HIP, R_HIP, L_WRIST, R_WRIST,
            L_ELBOW, R_ELBOW, L_KNEE, R_KNEE, L_ANKLE, R_ANKLE]

# -------------------------------------------------------------------- model
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
    """Where downloaded models live (override with TENNIS_COACH_CACHE)."""
    root = os.environ.get("TENNIS_COACH_CACHE")
    path = Path(root) if root else Path.home() / ".cache" / "tennis-form-coach"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_model(model_complexity: int = 2, progress: bool = True) -> Path:
    """Path to the pose model, downloading it on first use.

    Raises:
        RuntimeError: the model is absent and cannot be downloaded.
    """
    name, url = MODEL_URLS.get(model_complexity, MODEL_URLS[2])
    path = cache_dir() / name
    if path.exists() and path.stat().st_size > 0:
        return path

    if progress:
        print(f"  [模型] 首次執行，下載姿態模型 {name} ...")
    tmp = path.with_suffix(".part")
    try:
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(path)
    except Exception as e:                       # network, proxy, disk...
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"無法下載姿態模型 ({e})。可手動下載後放到 {path}："
            f"\n  {url}") from e
    return path


# ----------------------------------------------------------------- sequence
@dataclasses.dataclass
class PoseSequence:
    """Per-frame landmarks for one video.

    Attributes:
        world:  (T, 33, 3) metric landmarks, hip-centred. Used for all angles.
        pixel:  (T, 33, 2) image-space landmarks in pixels. Used for drawing.
        visibility: (T, 33) tracker confidence in [0, 1].
        detected:   (T,) True where the tracker found a person at all.
        fps, width, height: source video properties.
    """

    world: np.ndarray
    pixel: np.ndarray
    visibility: np.ndarray
    detected: np.ndarray
    fps: float
    width: int
    height: int

    @property
    def n_frames(self) -> int:
        return int(self.world.shape[0])

    @property
    def torso_scale(self) -> float:
        """Metres per torso length, the unit every normalised metric uses."""
        return geo.body_scale(self.world[:, L_SHOULDER], self.world[:, R_SHOULDER],
                              self.world[:, L_HIP], self.world[:, R_HIP])

    def quality(self) -> dict:
        """How much of this clip the tracker actually saw.

        `usable` is deliberately conservative: a clip that fails it still gets
        scored, but every caller is expected to say so out loud.
        """
        detected_ratio = float(np.mean(self.detected)) if self.n_frames else 0.0
        vis = self.visibility[:, CRITICAL]
        mean_visibility = float(np.mean(vis)) if vis.size else 0.0
        dropout = geo.longest_invalid_run(self.detected)
        return {
            "detected_ratio": round(detected_ratio, 3),
            "mean_visibility": round(mean_visibility, 3),
            "longest_dropout_frames": int(dropout),
            "usable": str(detected_ratio >= 0.80 and mean_visibility >= 0.50),
        }


def _clean(seq_world: np.ndarray, seq_pixel: np.ndarray, visibility: np.ndarray,
           detected: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Interpolate low-confidence landmarks, then smooth the trajectories."""
    world = np.array(seq_world, float, copy=True)
    pixel = np.array(seq_pixel, float, copy=True)

    for j in range(NUM_LANDMARKS):
        valid = detected & (visibility[:, j] >= MIN_VISIBILITY)
        world[:, j] = geo.interpolate_gaps(world[:, j], valid)
        pixel[:, j] = geo.interpolate_gaps(pixel[:, j], valid)

    return geo.smooth(world, window=7, poly=2), geo.smooth(pixel, window=7, poly=2)


def estimate_pose(video: str | Path, *, model_complexity: int = 2,
                  max_frames: int | None = None,
                  progress: bool = True) -> PoseSequence:
    """Track one person through a video file.

    Raises:
        FileNotFoundError: the video path does not exist.
        RuntimeError: the file cannot be decoded, or holds no readable frames.
    """
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision

    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(f"找不到影片檔: {video}")

    model_path = ensure_model(model_complexity, progress=progress)

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"無法開啟影片（編碼格式不支援？）: {video}")

    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    options = mp_vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
        running_mode=mp_vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    worlds: list[np.ndarray] = []
    pixels: list[np.ndarray] = []
    vis: list[np.ndarray] = []
    found: list[bool] = []

    last_world = np.zeros((NUM_LANDMARKS, 3))
    last_pixel = np.zeros((NUM_LANDMARKS, 2))

    idx = 0
    with mp_vision.PoseLandmarker.create_from_options(options) as landmarker:
        while True:
            if max_frames is not None and idx >= max_frames:
                break
            ok, frame = cap.read()
            if not ok:
                break

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = landmarker.detect_for_video(image, int(idx * 1000 / fps))

            if result.pose_world_landmarks and result.pose_landmarks:
                w = np.array([[p.x, p.y, p.z]
                              for p in result.pose_world_landmarks[0]], float)
                p2 = np.array([[p.x * width, p.y * height]
                               for p in result.pose_landmarks[0]], float)
                v = np.array([getattr(p, "visibility", 1.0)
                              for p in result.pose_landmarks[0]], float)
                last_world, last_pixel = w, p2
                worlds.append(w)
                pixels.append(p2)
                vis.append(v)
                found.append(True)
            else:
                # Hold the last known pose so array shapes stay rectangular;
                # `detected=False` marks it for interpolation downstream.
                worlds.append(last_world)
                pixels.append(last_pixel)
                vis.append(np.zeros(NUM_LANDMARKS))
                found.append(False)

            idx += 1
            if progress and idx % 30 == 0:
                print(f"\r  [姿態] 已處理 {idx} 幀...", end="", flush=True)

    cap.release()
    if progress:
        print(f"\r  [姿態] 完成，共 {idx} 幀              ")

    if idx == 0:
        raise RuntimeError(f"影片中沒有可讀取的畫面: {video}")

    world = np.stack(worlds)
    pixel = np.stack(pixels)
    visibility = np.stack(vis)
    detected = np.array(found, bool)

    world, pixel = _clean(world, pixel, visibility, detected)
    return PoseSequence(world=world, pixel=pixel, visibility=visibility,
                        detected=detected, fps=fps, width=width, height=height)
