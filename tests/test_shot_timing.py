"""Tests for ml/shot_timing.py (M2: pose-swing-speed half of shot-timing
detection -- see ml/README.md for what's blocked/not covered here).

Ground truth comes from tests/synth_pose.py's normal-CDF-ramp fixture, which
gives an exact, known contact frame per synthetic swing -- not from
human-labeled footage (data-labeler has not produced any labels yet; see
ml/README.md). These tests are a correctness check on the detector's logic,
not an accuracy claim about real footage.
"""
from __future__ import annotations

import numpy as np
import pytest

from cv.pose_overlay import L_WRIST, R_WRIST
from ml.shot_timing import combined_wrist_speed, detect_shots
from tests.synth_pose import synth_landmark_sequence

FPS = 30.0


def test_detects_single_swing_at_exact_frame():
    seq = synth_landmark_sequence([60], fps=FPS)
    result = detect_shots(seq)

    assert result.shot_count == 1
    assert result.events[0].contact_frame == pytest.approx(60, abs=2)
    assert result.events[0].wrist == "right"


def test_detects_correct_count_for_multiple_swings():
    contacts = [40, 110, 180, 250]
    seq = synth_landmark_sequence(contacts, fps=FPS)
    result = detect_shots(seq)

    assert result.shot_count == len(contacts)
    detected = [e.contact_frame for e in result.events]
    for truth, found in zip(contacts, detected):
        assert found == pytest.approx(truth, abs=2)


def test_recovery_motion_is_not_counted_as_a_swing():
    # A single swing's own recovery ramp (see synth_pose.py) should not be
    # picked up as a second shot -- it's ~8x wider, i.e. a much lower peak
    # speed, than the real contact.
    seq = synth_landmark_sequence([50], fps=FPS)
    result = detect_shots(seq)
    assert result.shot_count == 1


def test_left_handed_swing_is_detected_without_assuming_handedness():
    seq = synth_landmark_sequence([70], fps=FPS, wrist_idx=L_WRIST)
    result = detect_shots(seq)

    assert result.shot_count == 1
    assert result.events[0].contact_frame == pytest.approx(70, abs=2)
    assert result.events[0].wrist == "left"


def test_combined_wrist_speed_picks_the_faster_wrist_per_frame():
    seq = synth_landmark_sequence([50], fps=FPS, wrist_idx=R_WRIST)
    speed, which = combined_wrist_speed(seq)

    assert speed.shape == (seq.frame_count,)
    assert which.shape == (seq.frame_count,)
    # At the contact frame the right wrist should be identified as faster.
    assert which[50] == "right"
    assert speed[50] > 0


def test_no_motion_yields_no_shots():
    seq = synth_landmark_sequence([], n_frames=60, fps=FPS)
    result = detect_shots(seq)
    assert result.shot_count == 0


def test_too_short_sequence_returns_no_shots_without_crashing():
    seq = synth_landmark_sequence([2], n_frames=3, fps=FPS)
    result = detect_shots(seq)
    assert result.shot_count == 0
    assert result.speed.shape == (3,)


def test_detection_gaps_do_not_register_as_false_shots():
    """If the player briefly drops out of detection (NaN landmarks), that
    gap must not itself look like a swing once landmarks resume -- a real
    occlusion/false-negative frame, not a fast arm motion."""
    seq = synth_landmark_sequence([80], fps=FPS)
    # Blank out a chunk of frames far from the real swing.
    seq.landmarks[20:30] = np.nan
    seq.detected[20:30] = False

    result = detect_shots(seq)
    assert result.shot_count == 1
    assert result.events[0].contact_frame == pytest.approx(80, abs=2)
