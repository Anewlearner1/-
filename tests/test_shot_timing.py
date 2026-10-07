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


# ------------------------------------------------- racket-hand-only mode
def _two_hand_sequence():
    """Right wrist swings hard at 60 and 140; the left wrist makes a smaller
    swing at 100 that no stroke accompanies (an off-hand flourish)."""
    right = synth_landmark_sequence([60, 140], fps=FPS, n_frames=220,
                                    wrist_idx=R_WRIST, amplitude_px=300.0)
    left = synth_landmark_sequence([100], fps=FPS, n_frames=220,
                                   wrist_idx=L_WRIST, amplitude_px=200.0)
    right.landmarks[:, L_WRIST] = left.landmarks[:, L_WRIST]
    return right


def test_default_mode_counts_the_off_hand_flourish_as_a_shot():
    """Documents the over-firing the hand option exists to fix."""
    assert detect_shots(_two_hand_sequence()).shot_count == 3


@pytest.mark.parametrize("hand", ["right", "auto"])
def test_following_the_racket_hand_ignores_the_off_hand(hand):
    result = detect_shots(_two_hand_sequence(), hand=hand)
    assert [e.contact_frame for e in result.events] == pytest.approx([60, 140], abs=2)
    assert all(e.wrist == "right" for e in result.events)


def test_following_the_wrong_hand_finds_only_that_hands_motion():
    result = detect_shots(_two_hand_sequence(), hand="left")
    assert [e.contact_frame for e in result.events] == pytest.approx([100], abs=2)


def test_infer_racket_hand_uses_peak_speed():
    from ml.shot_timing import infer_racket_hand
    assert infer_racket_hand(_two_hand_sequence()) == "right"
    left_handed = synth_landmark_sequence([60, 140], fps=FPS, n_frames=220,
                                          wrist_idx=L_WRIST)
    assert infer_racket_hand(left_handed) == "left"


def test_an_invalid_hand_value_is_rejected():
    with pytest.raises(ValueError):
        detect_shots(_two_hand_sequence(), hand="both")


# ------------------------------------------------------------ merge_within_s
def test_merge_drops_a_follow_through_peak_and_keeps_the_earlier_event():
    seq = synth_landmark_sequence([60, 72], fps=FPS, n_frames=160)
    assert detect_shots(seq).shot_count == 2                 # 0.4 s apart: both found
    merged = detect_shots(seq, merge_within_s=0.5)
    assert [e.contact_frame for e in merged.events] == pytest.approx([60], abs=2)


def test_merge_keeps_hits_further_apart_than_the_window():
    seq = synth_landmark_sequence([60, 80], fps=FPS, n_frames=160)
    assert detect_shots(seq, merge_within_s=0.5).shot_count == 2   # 0.67 s apart


def test_merge_within_s_must_be_positive():
    with pytest.raises(ValueError):
        detect_shots(synth_landmark_sequence([60], fps=FPS), merge_within_s=0)
