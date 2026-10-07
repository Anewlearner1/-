"""Tests for ml/ball_filter.py on a small synthetic video with known ball frames."""
import numpy as np
import pytest
cv2 = pytest.importorskip("cv2")

from cv.pose_overlay import NUM_LANDMARKS, PlayerLandmarkSequence
from ml.ball_filter import ball_frames_near_wrists, filter_shots_with_ball
from ml.shot_timing import ShotEvent, ShotTimingResult

FPS, N, W, H = 30.0, 60, 240, 240
BALL_BGR = (40, 230, 230)
WRIST = (40.0, 40.0)


def _landmarks():
    lm = np.full((N, NUM_LANDMARKS, 2), np.nan)
    lm[:, 11] = lm[:, 12] = (40.0, 20.0)     # shoulders
    lm[:, 23] = lm[:, 24] = (40.0, 50.0)     # hips -> torso 30 px, search radius 60 px
    lm[:, 15] = lm[:, 16] = WRIST
    return lm


@pytest.fixture
def video(tmp_path):
    """Ball beside the wrist on frames 8-12; a ball far from the wrist on 38-42."""
    path = tmp_path / "v.mp4"
    wr = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    if not wr.isOpened():
        pytest.skip("mp4 encoder unavailable")
    for f in range(N):
        im = np.full((H, W, 3), (120, 60, 20), np.uint8)
        if 8 <= f <= 12:
            cv2.circle(im, (60, 45), 5, BALL_BGR, -1)
        if 38 <= f <= 42:
            cv2.circle(im, (210, 210), 5, BALL_BGR, -1)
        wr.write(im)
    wr.release()
    return path


def _result(frames):
    return ShotTimingResult(events=[ShotEvent(f, f / FPS, 100.0, "right") for f in frames],
                            speed=np.zeros(N), fps=FPS)


def test_counts_frames_with_a_ball_near_a_wrist(video):
    lm = _landmarks()
    assert ball_frames_near_wrists(video, lm, 10, window=4) >= 4
    assert ball_frames_near_wrists(video, lm, 40, window=4) == 0     # ball too far away
    assert ball_frames_near_wrists(video, lm, 25, window=4) == 0     # no ball at all


def test_filter_keeps_only_events_with_a_ball(video):
    lm = _landmarks()
    seq = PlayerLandmarkSequence(lm, np.ones((N, NUM_LANDMARKS)), np.ones(N, bool), FPS, W, H)
    filtered, counts = filter_shots_with_ball(video, seq, _result([10, 25, 40]))
    assert [e.contact_frame for e in filtered.events] == [10]
    assert set(counts) == {10, 25, 40} and counts[25] == 0


def test_min_ball_frames_must_be_positive(video):
    lm = _landmarks()
    seq = PlayerLandmarkSequence(lm, np.ones((N, NUM_LANDMARKS)), np.ones(N, bool), FPS, W, H)
    with pytest.raises(ValueError):
        filter_shots_with_ball(video, seq, _result([10]), min_ball_frames=0)


def test_default_window_is_fps_scaled_and_unchanged_at_30fps(monkeypatch):
    from ml import ball_filter as bf
    seen = []
    monkeypatch.setattr(bf, "ball_frames_near_wrists",
                        lambda video, lm, frame, window: seen.append(window) or 1)
    for fps in (29.97, 30.0, 60.0, 120.0):
        seq = type("S", (), {"fps": fps, "landmarks": None})()
        bf.filter_shots_with_ball("v.mp4", seq, _result([10]))
    assert seen == [8, 8, 16, 32]
