"""Unit tests for the ball blob finder and trajectory helpers (synthetic frames only)."""
import cv2
import numpy as np

from ml.ball_experiment import horizontal_reversal, nearest_ball_x
from ml.ball_filter import find_ball_blob

BALL_BGR = (40, 230, 230)     # yellow-green in BGR
WRIST = (100.0, 100.0)


def _frame():
    return np.full((200, 200, 3), (120, 60, 20), np.uint8)    # blue "court"


def test_a_ball_coloured_dot_next_to_the_wrist_is_found():
    im = _frame(); cv2.circle(im, (115, 100), 4, BALL_BGR, -1)
    assert find_ball_blob(im, [WRIST], radius_px=30) is not None


def test_the_same_dot_far_from_every_wrist_is_ignored():
    im = _frame(); cv2.circle(im, (180, 180), 4, BALL_BGR, -1)
    assert find_ball_blob(im, [WRIST], radius_px=30) is None


def test_a_large_yellow_region_is_not_a_ball():
    im = _frame(); cv2.rectangle(im, (80, 80), (140, 140), BALL_BGR, -1)
    assert find_ball_blob(im, [WRIST], radius_px=60) is None


def test_non_ball_colours_and_missing_wrists_return_none():
    im = _frame(); cv2.circle(im, (115, 100), 4, (0, 0, 220), -1)   # red
    assert find_ball_blob(im, [WRIST], radius_px=30) is None
    im2 = _frame(); cv2.circle(im2, (115, 100), 4, BALL_BGR, -1)
    assert find_ball_blob(im2, [(float("nan"), float("nan"))], radius_px=30) is None


def test_a_ball_that_comes_in_and_goes_back_is_a_reversal():
    track = [(125, 335.0), (126, 292.0), (127, 258.0), (128, 221.0), (129, 184.0), (130, 225.0), (131, 270.0)]
    assert horizontal_reversal(track) is True


def test_a_ball_that_passes_by_is_not_a_reversal():
    assert horizontal_reversal([(f, 300.0 - 30 * i) for i, f in enumerate(range(10, 18))]) is False


def test_too_few_ball_points_is_undecidable():
    assert horizontal_reversal([(1, 10.0), (2, 40.0), (3, 70.0)]) is None


def test_nearest_ball_x_returns_the_blob_closest_to_a_wrist():
    im = _frame()
    cv2.circle(im, (115, 100), 4, BALL_BGR, -1)
    cv2.circle(im, (160, 100), 4, BALL_BGR, -1)
    assert abs(nearest_ball_x(im, [WRIST], radius_px=80) - 115) < 2
