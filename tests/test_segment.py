"""Tests for swing detection and phase labelling.

These guard three fixes that real match footage forced and that are easy to
regress: a swing's preparation window must not reach into the previous
swing's follow-through, the swing direction must be built from pre-contact
frames only, and swings excluded for having no backswing must be counted
rather than silently disappearing.
"""
import numpy as np
import pytest

from synth import synth_swing_sequence
from tennis_coach import geometry as geo
from tennis_coach.features import extract_features
from tennis_coach.segment import PHASE_ORDER, active_region, find_swings


@pytest.fixture(scope="module")
def one_swing():
    seq = synth_swing_sequence(n_swings=1)
    feats = extract_features(seq)
    return feats, find_swings(feats)


# ------------------------------------------------------------ active_region
def test_active_region_bridges_a_short_lull():
    # Two humps separated by 3 quiet frames: one swing, not two.
    speed = np.array([0, 5, 0, 0, 0, 9, 0, 0, 0, 0, 0, 0, 0], float)
    lo, hi = active_region(speed, frame=5, floor=1.0, min_quiet=6)
    assert lo == 1 and hi == 5


def test_active_region_stops_at_a_long_pause():
    speed = np.array([0, 5, 0, 0, 0, 0, 0, 0, 9, 0], float)
    lo, hi = active_region(speed, frame=8, floor=1.0, min_quiet=4)
    assert lo == 8, "長時間靜止後不應把前一次動作併進來"


def test_active_region_clamps_at_the_array_edges():
    speed = np.full(6, 10.0)
    assert active_region(speed, frame=3, floor=1.0, min_quiet=3) == (0, 5)


# ------------------------------------------------------------- find_swings
def test_detects_the_expected_number_of_swings():
    for n in (1, 2, 3):
        feats = extract_features(synth_swing_sequence(n_swings=n))
        assert len(find_swings(feats)) == n


def test_returns_nothing_for_a_motionless_clip():
    seq = synth_swing_sequence(n_swings=1)
    seq.world[:] = seq.world[0]          # freeze every frame
    assert find_swings(extract_features(seq)) == []


def test_handles_a_clip_that_is_too_short():
    seq = synth_swing_sequence(n_swings=1)
    seq.world = seq.world[:3]
    seq.pixel = seq.pixel[:3]
    seq.visibility = seq.visibility[:3]
    seq.detected = seq.detected[:3]
    assert find_swings(extract_features(seq)) == []


def test_contact_is_the_fastest_frame(one_swing):
    feats, swings = one_swing
    speed = geo.smooth(feats["wrist_speed"], window=5, poly=2)
    assert swings[0].contact == int(np.argmax(speed))


def test_phase_frames_are_ordered(one_swing):
    _, swings = one_swing
    s = swings[0]
    assert s.start <= s.turn_start <= s.takeback <= s.contact <= s.follow_end <= s.end


def test_the_swing_includes_its_preparation(one_swing):
    """The backswing is far slower than the forward swing; it must not be clipped."""
    _, swings = one_swing
    s = swings[0]
    assert s.takeback > s.start, "引拍階段被切掉了，準備類指標將無法量測"
    assert s.contact - s.turn_start > 5


def test_phases_cover_the_whole_swing_without_gaps(one_swing):
    _, swings = one_swing
    s = swings[0]
    covered = [s.phase_at(f) for f in range(s.start, s.end + 1)]
    assert all(p is not None for p in covered)
    seen = [p for i, p in enumerate(covered) if i == 0 or p != covered[i - 1]]
    assert seen == sorted(set(seen), key=PHASE_ORDER.index)


def test_phase_at_returns_none_outside_the_swing(one_swing):
    _, swings = one_swing
    assert swings[0].phase_at(swings[0].start - 1) is None


def test_swing_direction_is_a_unit_ground_vector(one_swing):
    _, swings = one_swing
    d = swings[0].swing_dir
    assert d[1] == 0.0, "揮拍方向應投影在地面上"
    assert np.linalg.norm(d) == pytest.approx(1.0)


def test_swings_come_back_in_time_order():
    feats = extract_features(synth_swing_sequence(n_swings=3))
    contacts = [s.contact for s in find_swings(feats)]
    assert contacts == sorted(contacts)


def test_swings_do_not_overlap():
    feats = extract_features(synth_swing_sequence(n_swings=3))
    swings = find_swings(feats)
    for a, b in zip(swings, swings[1:]):
        assert a.end < b.start or a.contact < b.start


def test_prep_window_never_reaches_into_the_previous_swing():
    """A swing's start must never precede the previous swing's follow_end.

    Regression guard for a real bug: the boundary used to be the previous
    swing's *contact* frame, not its follow_end. In fast rally footage with
    contacts under a second apart, that let this swing's preparation window
    reach back into the previous stroke's still-ongoing follow-through, so
    prep-phase metrics measured the wrong stroke. Verified against real match
    footage: 40% of swings in the calibration corpus were contaminated.
    """
    for pad in (3, 8, 15, 30, 50):
        feats = extract_features(synth_swing_sequence(n_swings=4, pad_frames=pad))
        swings = find_swings(feats)
        for a, b in zip(swings, swings[1:]):
            assert b.start > a.follow_end, (
                f"pad={pad}: swing start {b.start} 落在前一拍隨揮結束 {a.follow_end} 之前或同時")


# ------------------------------------------------------------- diagnostics
def test_diagnostics_reports_no_drops_in_the_clean_case():
    feats = extract_features(synth_swing_sequence(n_swings=3))
    diag = {}
    swings = find_swings(feats, diagnostics=diag)
    assert diag["n_candidates"] == len(swings)
    assert diag["n_dropped"] == 0


def test_diagnostics_safety_valve_zeroes_n_dropped():
    """When every candidate fails min_backswing_travel, the safety valve keeps
    them all (scoring something beats scoring nothing) -- n_dropped must
    reflect that nothing was actually excluded from the returned list, not
    the raw pre-safety-valve rejection count.
    """
    feats = extract_features(synth_swing_sequence(n_swings=3))
    diag = {}
    swings = find_swings(feats, diagnostics=diag, min_backswing_travel=1e9)
    assert len(swings) == diag["n_candidates"]
    assert diag["n_dropped"] == 0
