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
