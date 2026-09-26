"""Tests for stroke classification (forehand / backhand / serve)."""
from synth import synth_swing_sequence
from tennis_coach.classify import classify_stroke
from tennis_coach.features import extract_features
from tennis_coach.segment import find_swings


def _first_swing(**kwargs):
    seq = synth_swing_sequence(n_swings=1, **kwargs)
    feats = extract_features(seq)
    swings = find_swings(feats)
    assert swings
    return feats, swings[0]


def test_a_racket_side_takeback_is_a_forehand():
    feats, swing = _first_swing(takeback_lateral=0.50, overhead=False)
    assert classify_stroke(feats, swing) == "forehand"


def test_a_crossed_over_takeback_is_a_backhand():
    feats, swing = _first_swing(takeback_lateral=-0.50, overhead=False)
    assert classify_stroke(feats, swing) == "backhand"


def test_an_overhead_contact_is_a_serve():
    # contact_height is measured above the HIPS, and the head sits roughly
    # 1.5 torso lengths above the hips by the time the fixture's recovery
    # ramp (which bleeds into every swing, even before contact) is accounted
    # for -- so it takes a generously high contact_height to clear it.
    feats, swing = _first_swing(overhead=True, contact_height=2.5)
    assert classify_stroke(feats, swing) == "serve"


def test_classification_does_not_depend_on_which_hand_is_the_racket_hand():
    right, right_swing = _first_swing(hand="right", takeback_lateral=0.50)
    left, left_swing = _first_swing(hand="left", takeback_lateral=0.50)
    assert classify_stroke(right, right_swing) == classify_stroke(left, left_swing)


def test_a_serve_with_a_noisy_head_landmark_still_classifies_as_serve():
    """Regression guard for a real misclassification: on a second serve in
    real footage, the same contact height read as clearly overhead relative
    to the hips (1.77 torso lengths) but fell just under the head-relative
    threshold (0.25 vs 0.3) purely because the player's head angle shifted a
    little between serves. wrist_above_head alone flipped this to "forehand";
    the hip-relative check must catch it even when the head signal doesn't.
    """
    feats, swing = _first_swing(overhead=True, contact_height=2.1)
    assert feats["wrist_above_head"][swing.contact] < 0.3, "夾具前提：頭部訊號本身應該不足以觸發判定"
    assert classify_stroke(feats, swing) == "serve"
