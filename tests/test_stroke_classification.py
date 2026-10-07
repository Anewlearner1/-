"""Tests for ml/stroke_classification.py and ml/eval_stroke_classification.py.

HONESTY NOTE: every fixture here is SYNTHETIC (tests/synth_pose.py's
synth_stroke_sequence), built from our own assumption of what forehand /
backhand / overhead look like in 2D. These tests verify the classifier's
LOGIC (sign conventions, handedness handling, NaN safety, which signals are
and are not used). They are NOT evidence of accuracy on real footage, and no
real-footage accuracy exists yet (no labeled clips).
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest

from cv.pose_overlay import L_SHOULDER, L_WRIST, R_SHOULDER, R_WRIST
from ml.eval_stroke_classification import evaluate, format_report, main, save_landmarks
from ml.shot_timing import ShotEvent, detect_shots
from ml.stroke_classification import (
    GAP_CONF, WRIST_GAP_BACKHAND, classify_shot, classify_shots, detect_racket_hand,
)
from tests.synth_pose import synth_stroke_sequence

FIX = Path(__file__).parent / "fixtures"
STROKES = [(70, "forehand"), (140, "backhand"), (210, "overhead"), (280, "backhand")]
TRUTH = {"forehand": "forehand", "backhand": "backhand", "overhead": "other"}


def ev(frame: int, wrist: str = "right") -> ShotEvent:
    return ShotEvent(frame, frame / 30.0, 500.0, wrist)


@pytest.mark.parametrize("hand", ["right", "left"])
@pytest.mark.parametrize("facing", ["camera", "away"])
def test_labels_correct_for_both_hands_and_facings(hand, facing):
    """Logic check (synthetic): same labels for L/R-handed and either facing,
    both with the hand given and with it inferred."""
    seq = synth_stroke_sequence(STROKES, hand=hand, facing=facing)
    events = detect_shots(seq).events
    assert [e.contact_frame for e in events] == pytest.approx([c for c, _ in STROKES], abs=2)
    expected = [TRUTH[k] for _, k in STROKES]
    for given in (hand, None):
        res = classify_shots(seq, events, hand=given)
        assert [r.label for r in res] == expected
        assert all(r.hand == hand for r in res)
        assert all(r.confidence > 0.5 for r in res)
    assert classify_shots(seq, events)[0].hand_source == "inferred"
    assert classify_shots(seq, events, hand=hand)[0].hand_source == "given"


def test_event_wrist_field_and_faster_wrist_do_not_change_label():
    """The label must not depend on which wrist peaked: (a) overwrite
    event.wrist; (b) make the OFF wrist the faster one in the data."""
    seq = synth_stroke_sequence(STROKES[:2], hand="right", off_wrist_faster=True)
    events = [ev(70), ev(140)]
    base = [r.label for r in classify_shots(seq, events, hand="right")]
    assert base == ["forehand", "backhand"]
    for w in ("left", "right", "unknown"):
        flipped = [dataclasses.replace(e, wrist=w) for e in events]
        assert [r.label for r in classify_shots(seq, flipped, hand="right")] == base
    # the detector really did report the off wrist as faster here
    detected = detect_shots(seq).events
    assert all(e.wrist == "left" for e in detected if abs(e.contact_frame - 70) <= 2 or abs(e.contact_frame - 140) <= 2)


def test_inferred_hand_can_be_wrong_but_override_fixes_it():
    """Peak-speed hand inference is a heuristic: when the off wrist is faster
    it picks the wrong hand (documented limitation); `hand=` overrides it."""
    seq = synth_stroke_sequence(STROKES[:2], hand="right", off_wrist_faster=True)
    hand, _ratio = detect_racket_hand(seq)
    assert hand == "left"                       # wrong on purpose-built data
    assert classify_shot(seq, ev(70)).hand == "left"
    assert classify_shot(seq, ev(70), hand="right").label == "forehand"


def test_invalid_hand_rejected():
    seq = synth_stroke_sequence(STROKES[:1])
    with pytest.raises(ValueError):
        classify_shot(seq, ev(70), hand="both")


def test_overhead_is_other_regardless_of_lateral_side():
    """Fixture's overhead takeback is cross-body (would read backhand); the
    height gate must still yield 'other' -- serves not forced into FH/BH."""
    seq = synth_stroke_sequence([(70, "overhead")])
    r = classify_shot(seq, ev(70), hand="right")
    assert r.label == "other"
    assert r.wrist_above_head > 0.3


def test_result_exposes_debug_signals():
    seq = synth_stroke_sequence(STROKES[:2])
    fh, bh = classify_shots(seq, [ev(70), ev(140)], hand="right")
    assert fh.takeback_lateral > 0 > bh.takeback_lateral
    assert fh.margin == pytest.approx(abs(fh.takeback_lateral))
    assert fh.takeback_frame is not None and fh.takeback_frame < 70


def test_nan_frames_at_contact_give_unknown_not_a_guess():
    seq = synth_stroke_sequence(STROKES[:2])
    seq.landmarks[60:80] = np.nan
    seq.detected[60:80] = False
    r = classify_shot(seq, ev(70), hand="right")
    assert r.label == "unknown" and r.confidence == 0.0 and r.reason


def test_nan_in_backswing_window_does_not_crash():
    seq = synth_stroke_sequence(STROKES[:2])
    seq.landmarks[55:68, R_WRIST] = np.nan       # racket wrist lost mid backswing
    r = classify_shot(seq, ev(70), hand="right")
    assert r.label in ("forehand", "backhand", "other", "unknown")
    seq.landmarks[40:70, R_WRIST] = np.nan       # whole window gone
    r = classify_shot(seq, ev(70), hand="right")    # contact still visible -> gap fallback only
    assert "wrist-gap fallback" in r.reason and r.confidence == GAP_CONF
    seq.landmarks[40:80, R_WRIST] = np.nan       # contact gone too: no gap, no guess
    assert classify_shot(seq, ev(70), hand="right").label == "unknown"


def test_low_visibility_treated_as_missing():
    seq = synth_stroke_sequence(STROKES[:2])
    seq.visibility[:, R_WRIST] = 0.1
    assert classify_shot(seq, ev(70), hand="right").label == "unknown"


def test_all_nan_clip_and_bad_frames_and_empty_events():
    seq = synth_stroke_sequence(STROKES[:2])
    assert classify_shots(seq, []) == []
    assert classify_shot(seq, ev(10_000), hand="right").label == "unknown"
    assert classify_shot(seq, ev(-1), hand="right").label == "unknown"
    seq.landmarks[:] = np.nan
    seq.detected[:] = False
    assert classify_shot(seq, ev(70)).label == "unknown"         # hand inferred, no motion
    assert [r.label for r in classify_shots(seq, [ev(70)])] == ["unknown"]


def _wrists_together(seq, lo, hi):
    seq.landmarks[lo:hi, L_WRIST] = seq.landmarks[lo:hi, R_WRIST] + [5.0, 0.0]


def test_edge_on_shoulders_fall_back_to_wrist_gap():
    """Side-on camera: shoulders overlap in 2D, the takeback rule abstains and
    the low-confidence wrist-gap fallback answers instead."""
    seq = synth_stroke_sequence(STROKES[:2])
    seq.landmarks[:, L_SHOULDER, 0] = seq.landmarks[:, R_SHOULDER, 0]
    r = classify_shot(seq, ev(70), hand="right")
    assert "edge-on" in r.reason and "wrist-gap fallback" in r.reason
    assert r.confidence == GAP_CONF
    assert r.label == ("backhand" if r.wrist_gap < WRIST_GAP_BACKHAND else "forehand")
    _wrists_together(seq, 60, 80)
    assert classify_shot(seq, ev(70), hand="right").label == "backhand"


def test_wrists_together_veto_the_serve_gate():
    """A two-handed stroke can reach high; with both wrists together it is not a serve."""
    seq = synth_stroke_sequence([(70, "overhead")])
    _wrists_together(seq, 60, 80)
    r = classify_shot(seq, ev(70), hand="right")
    assert r.label != "other" and r.wrist_gap < WRIST_GAP_BACKHAND


def test_tiebreak_uses_contact_side_when_takeback_ambiguous():
    seq = synth_stroke_sequence([(70, "forehand")])
    # flatten the wrist onto the torso midline throughout the backswing, keep
    # the contact-side offset: takeback ambiguous, contact still racket-side
    hip_x = 640.0
    w = seq.landmarks[:, R_WRIST, 0]
    rd = np.sign(seq.landmarks[0, R_SHOULDER, 0] - seq.landmarks[0, L_SHOULDER, 0])
    w[40:69] = hip_x
    w[69:72] = hip_x + rd * 0.4 * 120
    r = classify_shot(seq, ev(70), hand="right")
    assert abs(r.takeback_lateral) < 0.15
    assert r.used_tiebreak and r.label == "forehand"
    assert r.confidence <= 0.5


def test_ambiguous_everywhere_uses_gap_fallback():
    seq = synth_stroke_sequence([(70, "forehand")])
    seq.landmarks[40:75, R_WRIST, 0] = 640.0
    r = classify_shot(seq, ev(70), hand="right")
    assert "wrist-gap fallback" in r.reason and r.label in ("forehand", "backhand")


def test_weak_hand_inference_lowers_confidence():
    seq = synth_stroke_sequence([(70, "forehand")])
    strong = classify_shot(seq, ev(70))                 # off wrist still -> huge ratio
    seq2 = synth_stroke_sequence([(70, "forehand")], off_wrist_faster=True)
    # both wrists move at similar speed -> ratio ~1.5 > threshold; force ~1 case:
    seq2.landmarks[:, 15] = seq2.landmarks[:, 16]       # off wrist copies racket wrist
    weak = classify_shot(seq2, ev(70))
    assert weak.confidence < strong.confidence


def test_custom_classifier_plugs_in_behind_same_interface():
    seq = synth_stroke_sequence(STROKES[:2])
    stub = lambda s, e, h: dataclasses.replace(classify_shot(s, e, h), label="other")
    assert [r.label for r in classify_shots(seq, [ev(70)], hand="right", classifier=stub)] == ["other"]


# ----------------------------------------------------------------- eval script
def test_eval_script_runs_on_handmade_fixture_and_flags_non_real(tmp_path, capsys):
    """Runs the evaluator end to end on a synthetic, hand-made fixture. The
    numbers prove only that the plumbing works."""
    labels = FIX / "stroke_eval_labels.json"
    seq = synth_stroke_sequence(STROKES, hand="right")
    save_landmarks(seq, tmp_path / "synth_stroke_clip.npz")

    res = evaluate([labels, FIX / "stroke_eval_labels_unlabeled.json"], landmarks_dir=tmp_path)
    assert res["clips_evaluated"] == 1
    assert res["clips_skipped_no_stroke_labels"] == ["clip_no_strokes"]
    assert res["real_footage"] is False
    assert res["n_shots"] == 4
    assert res["confusion"]["backhand"]["backhand"] == 2

    det = evaluate([labels], landmarks_dir=tmp_path, mode="detected")
    assert det["n_shots"] == 4

    rc = main([str(labels), "--landmarks-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "NOT REAL-FOOTAGE" in out
    assert "NOT REAL-FOOTAGE" in format_report(res)


def test_eval_rejects_malformed_labels(tmp_path):
    seq = synth_stroke_sequence(STROKES[:1])
    save_landmarks(seq, tmp_path / "c.npz")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"video_id": "c", "fps": 30, "contact_frames": [70],
                               "stroke_labels": ["forehand", "backhand"]}))
    with pytest.raises(ValueError):
        evaluate([bad], landmarks_dir=tmp_path)
    bad.write_text(json.dumps({"video_id": "c", "fps": 30, "contact_frames": [70],
                               "stroke_labels": ["slice"]}))
    with pytest.raises(ValueError):
        evaluate([bad], landmarks_dir=tmp_path)


def test_side_on_serve_is_still_other_not_a_gap_guess():
    """The serve gate runs before the edge-on check, so the fallback cannot
    turn a side-on serve (off arm far away -> big gap) into a forehand."""
    seq = synth_stroke_sequence([(70, "overhead")])
    seq.landmarks[:, L_SHOULDER, 0] = seq.landmarks[:, R_SHOULDER, 0]
    assert classify_shot(seq, ev(70), hand="right").label == "other"


def _with_world(seq, wrist_offset_m, hand="right"):
    """Attach synthetic world landmarks: hips 0.3 m apart along x, racket
    wrist `wrist_offset_m` toward the racket side (+x for right, -x for left)."""
    from cv.pose_overlay import L_HIP, R_HIP, L_WRIST as LW, R_WRIST as RW
    w = np.zeros((seq.frame_count, 33, 3))
    w[:, L_HIP] = [-0.15, 0, 0]
    w[:, R_HIP] = [0.15, 0, 0]
    side = 1.0 if hand == "right" else -1.0
    w[:, RW if hand == "right" else LW] = [side * wrist_offset_m, -0.3, 0.2]
    return dataclasses.replace(seq, world_landmarks=w)


@pytest.mark.parametrize("hand", ["right", "left"])
def test_3d_cue_decides_when_world_landmarks_exist(hand):
    from ml.stroke_classification import LATERAL_3D_FOREHAND_M
    seq = synth_stroke_sequence([(70, "forehand")])
    fh = classify_shot(_with_world(seq, LATERAL_3D_FOREHAND_M + 0.2, hand), ev(70), hand=hand)
    bh = classify_shot(_with_world(seq, LATERAL_3D_FOREHAND_M - 0.2, hand), ev(70), hand=hand)
    assert (fh.label, bh.label) == ("forehand", "backhand")
    assert "3D" in fh.reason and fh.lateral_3d == pytest.approx(LATERAL_3D_FOREHAND_M + 0.2)


def test_serve_gate_still_wins_over_the_3d_cue():
    seq = _with_world(synth_stroke_sequence([(70, "overhead")]), 0.6)
    assert classify_shot(seq, ev(70), hand="right").label == "other"


def test_without_world_landmarks_the_2d_rule_is_unchanged():
    seq = synth_stroke_sequence([(70, "forehand")])
    r = classify_shot(seq, ev(70), hand="right")
    assert r.lateral_3d is None and "3D" not in r.reason


def test_pose_lost_at_contact_gives_unknown_even_with_world_landmarks():
    """Serve gate cannot run without the contact pose, so the 3D cue must not
    answer either (a lost-arm serve must not become a full-confidence forehand)."""
    seq = _with_world(synth_stroke_sequence([(70, "overhead")]), 0.6)
    seq.landmarks[66:75] = np.nan
    seq.detected[66:75] = False
    r = classify_shot(seq, ev(70), hand="right")
    assert r.label == "unknown" and r.confidence == 0.0


def test_too_early_contact_falls_back_to_2d_rules():
    seq = _with_world(synth_stroke_sequence([(70, "forehand")]), 0.6)
    r = classify_shot(seq, ev(2), hand="right")
    assert r.lateral_3d is None and "3D" not in r.reason


def test_world_landmarks_survive_save_and_load(tmp_path):
    from ml.eval_stroke_classification import load_landmarks
    seq = _with_world(synth_stroke_sequence([(70, "forehand")]), 0.5)
    save_landmarks(seq, tmp_path / "c.npz")
    back = load_landmarks(tmp_path / "c.npz")
    assert np.array_equal(back.world_landmarks, seq.world_landmarks)
    save_landmarks(synth_stroke_sequence([(70, "forehand")]), tmp_path / "d.npz")
    assert load_landmarks(tmp_path / "d.npz").world_landmarks is None


def test_loco_3d_refits_per_clip_and_restores_the_constant(tmp_path):
    import ml.stroke_classification as sc
    from ml.eval_stroke_classification import evaluate_loco_3d
    before = sc.LATERAL_3D_FOREHAND_M
    paths = []
    for i, (fh_off, bh_off) in enumerate([(0.5, 0.1), (0.45, 0.15), (0.55, 0.05)]):
        seq = synth_stroke_sequence([(70, "forehand"), (140, "backhand")])
        w = _with_world(seq, fh_off).world_landmarks
        w[100:, 16, 0] = bh_off                    # right wrist offset for the second stroke
        seq = dataclasses.replace(seq, world_landmarks=w)
        save_landmarks(seq, tmp_path / f"v{i}.npz")
        lp = tmp_path / f"v{i}.json"
        lp.write_text(json.dumps({"video_id": f"v{i}", "fps": 30.0, "contact_frames": [70, 140],
                                  "stroke_labels": ["forehand", "backhand"], "racket_hand": "right"}))
        paths.append(lp)
    res = evaluate_loco_3d(paths, landmarks_dir=tmp_path)
    assert res["accuracy"] == 1.0 and res["clips_with_world_landmarks"] == 3
    assert sc.LATERAL_3D_FOREHAND_M == before
