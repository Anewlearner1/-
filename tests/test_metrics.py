"""Tests for per-swing technique metrics.

The synthetic fixture's ramps give exact ground truth for several inputs
(shoulder turn amount, knee flexion peak, contact height); these tests check
that `extract_metrics` recovers values close to what was asked for, not just
"some plausible number".
"""
import pytest

from synth import synth_swing_sequence
from tennis_coach.features import extract_features
from tennis_coach.metrics import extract_metrics
from tennis_coach.segment import find_swings


def _first_swing(**kwargs):
    seq = synth_swing_sequence(n_swings=1, **kwargs)
    feats = extract_features(seq)
    swings = find_swings(feats)
    assert swings, "夾具前提：合成揮拍必須被偵測到"
    return feats, swings[0]


def test_shoulder_turn_matches_the_requested_rotation():
    feats, swing = _first_swing(shoulder_turn=90.0)
    m = extract_metrics(feats, swing)
    assert m.shoulder_turn_deg == pytest.approx(90.0, abs=15.0)


def test_knee_bend_matches_the_requested_flexion_peak():
    feats, swing = _first_swing(knee_peak=40.0)
    m = extract_metrics(feats, swing)
    assert m.knee_bend_deg == pytest.approx(40.0, abs=10.0)


def test_contact_height_reflects_the_requested_wrist_height():
    low, low_swing = _first_swing(contact_height=0.10)
    high, high_swing = _first_swing(contact_height=0.70)
    low_m = extract_metrics(low, low_swing)
    high_m = extract_metrics(high, high_swing)
    assert high_m.contact_height > low_m.contact_height


def test_a_deeper_stance_gives_a_larger_stance_width():
    narrow, narrow_swing = _first_swing(stance_ratio=1.0)
    wide, wide_swing = _first_swing(stance_ratio=2.5)
    assert (extract_metrics(wide, wide_swing).stance_width
            > extract_metrics(narrow, narrow_swing).stance_width)

def test_more_weight_transfer_gives_a_larger_metric():
    low, low_swing = _first_swing(weight_transfer=0.05)
    high, high_swing = _first_swing(weight_transfer=0.6)
    assert (extract_metrics(high, high_swing).weight_transfer
            > extract_metrics(low, low_swing).weight_transfer)


def test_follow_through_time_is_positive_and_bounded_by_the_swing():
    feats, swing = _first_swing()
    m = extract_metrics(feats, swing)
    assert 0.0 <= m.follow_through_time_s <= swing.duration(feats.fps)


def test_to_dict_round_trips_every_field_as_a_float():
    feats, swing = _first_swing()
    d = extract_metrics(feats, swing).to_dict()
    assert set(d) == {
        "prep_time_s", "shoulder_turn_deg", "hip_shoulder_separation_deg",
        "knee_bend_deg", "contact_height", "contact_wrist_speed_kmh",
        "follow_through_time_s", "stance_width", "trunk_lean_deg",
        "weight_transfer",
    }
    assert all(isinstance(v, float) for v in d.values())
