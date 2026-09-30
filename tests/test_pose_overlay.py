"""End-to-end test for cv/pose_overlay.py.

Builds a short, real (decodable) mp4 from a still photo of a person -- not a
synthetic stick figure -- because MediaPipe's pose model is a trained neural
net and will not reliably fire on drawn shapes. The photo
(tests/fixtures/pose_sample.jpg) is Google's own MediaPipe pose-estimation
test asset (storage.googleapis.com/mediapipe-assets/pose.jpg, used in
MediaPipe's own examples/tests; Apache-2.0-licensed project), vendored here
so the test runs offline and deterministically.

This tests the real pipeline end-to-end: MediaPipe multi-pose detection ->
player_selection -> skeleton drawing -> video writing. It is intentionally
not a "didn't crash" smoke test -- it asserts pose was actually detected on
most frames.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

# tests/conftest.py puts the repo root on sys.path so `cv` is importable.
from cv.pose_overlay import generate_pose_overlay
from cv.player_selection import LargestCentralPlayerSelector

FIXTURE_IMAGE = Path(__file__).parent / "fixtures" / "pose_sample.jpg"
N_FRAMES = 12
FPS = 10.0


def _make_source_video(path: Path) -> None:
    """A short mp4 built from a real photo of a person, with slight per-frame
    jitter (simulating a hand-held phone) so it's not a frozen single image."""
    img = cv2.imread(str(FIXTURE_IMAGE))
    assert img is not None, f"could not decode fixture image: {FIXTURE_IMAGE}"
    h, w = img.shape[:2]

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, FPS, (w, h))
    if not writer.isOpened():
        pytest.skip("this environment lacks an mp4 encoder")

    for i in range(N_FRAMES):
        # tiny translation so consecutive frames aren't bit-identical
        dx, dy = (i % 3) - 1, (i % 5) - 2
        translation = np.float32([[1, 0, dx], [0, 1, dy]])
        frame = cv2.warpAffine(img, translation, (w, h))
        writer.write(frame)
    writer.release()


@pytest.fixture()
def source_video(tmp_path) -> Path:
    path = tmp_path / "source.mp4"
    _make_source_video(path)
    return path


def test_pose_overlay_runs_end_to_end(source_video, tmp_path):
    output_path = tmp_path / "overlay.mp4"

    result = generate_pose_overlay(
        source_video,
        output_path,
        model_complexity=0,          # lite model: fast, sufficient for this test
        num_candidate_poses=2,       # exercise the multi-person + selection path
        player_selector=LargestCentralPlayerSelector(),
        progress=False,
    )

    # 1. Output video exists and is non-empty.
    assert output_path.exists()
    assert output_path.stat().st_size > 0

    # 2. Same frame count / fps as the source.
    cap = cv2.VideoCapture(str(source_video))
    src_frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()

    assert result.frame_count == src_frame_count == N_FRAMES
    assert result.fps == pytest.approx(src_fps, abs=0.5)

    out_cap = cv2.VideoCapture(str(output_path))
    out_frame_count = int(out_cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out_fps = out_cap.get(cv2.CAP_PROP_FPS)
    out_cap.release()

    assert out_frame_count == N_FRAMES
    assert out_fps == pytest.approx(src_fps, abs=0.5)

    # 3. Pose was actually detected on a real fraction of frames, not "didn't
    #    crash" -- the fixture is a clear, unobstructed photo of one person,
    #    so detection should succeed on almost every frame.
    assert result.frames_with_pose > 0
    assert result.pose_detection_rate >= 0.7, (
        f"expected pose detected on most frames, got "
        f"{result.frames_with_pose}/{result.frame_count}")


def test_missing_video_raises_file_not_found(tmp_path):
    from cv.pose_overlay import generate_pose_overlay as gpo

    with pytest.raises(FileNotFoundError):
        gpo(tmp_path / "does_not_exist.mp4", tmp_path / "out.mp4", progress=False)
