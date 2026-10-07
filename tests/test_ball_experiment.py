"""Unit tests for the colour blob finder in ml/ball_experiment.py (synthetic frames only)."""
import cv2
import numpy as np

from ml.ball_experiment import find_ball_blob

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
