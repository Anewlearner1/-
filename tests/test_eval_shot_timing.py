"""Tests for ml/eval_shot_timing.py (pure functions; no video or pose needed)."""
import json

import pytest

from ml.eval_shot_timing import (evaluate_detected, evaluate_detected_seconds, load_label,
                                 match_contacts, match_one_to_one, seconds_to_frames, summarize)


def test_offset_sign_negative_means_detector_fired_early():
    m = match_contacts([129], [132])[0]
    assert m == {"truth": 132, "detected": 129, "offset": -3}


def test_a_contact_with_no_detections_is_a_miss_not_a_crash():
    m = match_contacts([], [50])[0]
    assert m["detected"] is None and m["offset"] is None
    assert summarize([m])["hit_rate_within_5"] == 0.0


def test_hit_rate_uses_the_tolerance():
    matches = match_contacts([97, 150], [100, 140])   # offsets -3 and +10
    s = summarize(matches, tolerances=(5, 10))
    assert s["hit_rate_within_5"] == 0.5 and s["hit_rate_within_10"] == 1.0
    assert s["mean_offset_frames"] == pytest.approx(3.5)


def test_partial_labels_never_report_precision(tmp_path):
    p = tmp_path / "clip.json"
    p.write_text(json.dumps({"video_id": "clip", "fps": 30.0, "contact_frames": [60]}))
    (tmp_path / "clip.meta.json").write_text(json.dumps({"complete": False}))
    res = evaluate_detected(load_label(p), [58, 200, 300])
    assert res["complete_labels"] is False
    assert "within_5" not in res and "within_10" not in res
    assert res["hit_rate_within_5"] == 1.0


def test_complete_labels_report_precision(tmp_path):
    p = tmp_path / "clip.json"
    p.write_text(json.dumps({"video_id": "clip", "fps": 30.0, "contact_frames": [60]}))
    res = evaluate_detected(load_label(p), [58, 200])
    assert res["within_5"]["precision"] == 0.5 and res["within_5"]["recall"] == 1.0


def test_label_missing_a_required_key_is_rejected(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"video_id": "x", "fps": 30.0}))
    with pytest.raises(ValueError):
        load_label(p)


def test_committed_labels_completeness_flags():
    from pathlib import Path
    labels = {p.name: load_label(p) for p in sorted(Path("labeling/labels").glob("*.json"))
              if not p.name.endswith(".meta.json")}
    assert len(labels) == 4
    for name, lab in labels.items():
        assert lab["contact_frames"]
        assert lab["complete"] is name.startswith(("1436d55e", "a7d5518f"))   # Fed 2 and Komura are partial


def test_a_duplicate_detection_of_one_stroke_is_a_false_positive():
    m = match_one_to_one([92, 100], [95], tol=10)
    assert m["tp"] == [(95, 92)] and m["fp"] == [100] and m["fn"] == []


def test_each_truth_is_used_once_and_far_detections_are_misses():
    m = match_one_to_one([50, 300], [52, 120], tol=5)
    assert m["tp"] == [(52, 50)] and m["fp"] == [300] and m["fn"] == [120]


def test_fons_label_is_now_complete_with_four_contacts():
    from pathlib import Path
    lab = load_label(Path("labeling/labels/1436d55e-Fonseca_side_view_practice_session.json"))
    assert lab["complete"] is True and lab["contact_frames"] == [26, 95, 164, 237]


def test_owner_rejected_frames_count_as_known_false_positives_even_when_partial(tmp_path):
    p = tmp_path / "clip.json"
    p.write_text(json.dumps({"video_id": "clip", "fps": 30.0, "contact_frames": [60]}))
    (tmp_path / "clip.meta.json").write_text(
        json.dumps({"complete": False, "not_contacts": [200]}))
    res = evaluate_detected(load_label(p), [58, 197, 400])
    assert res["known_false_positives"] == [197]
    assert "within_5" not in res          # still no precision for partial labels


def test_no_not_contacts_means_no_known_false_positive_field(tmp_path):
    p = tmp_path / "clip.json"
    p.write_text(json.dumps({"video_id": "clip", "fps": 30.0, "contact_frames": [60]}))
    assert "known_false_positives" not in evaluate_detected(load_label(p), [58])


def test_the_same_seconds_tolerance_is_ten_frames_at_30fps_and_twenty_at_60():
    assert seconds_to_frames(0.33, 30.0) == 10
    assert seconds_to_frames(0.33, 29.97) == 10
    assert seconds_to_frames(0.33, 60.0) == 20
    assert seconds_to_frames(0.001, 30.0) == 1


def _label(tmp_path, fps, contacts, complete=True):
    p = tmp_path / "clip.json"
    p.write_text(json.dumps({"video_id": "clip", "fps": fps, "contact_frames": contacts}))
    (tmp_path / "clip.meta.json").write_text(json.dumps({"complete": complete}))
    return load_label(p)


def test_a_detection_nine_frames_off_matches_at_30fps_but_not_at_60fps(tmp_path):
    at30 = evaluate_detected_seconds(_label(tmp_path, 30.0, [100]), [109])
    at60 = evaluate_detected_seconds(_label(tmp_path, 60.0, [100]), [109])
    assert at30["recall"] == 1.0 and at30["tolerance_frames"] == 10
    assert at60["recall"] == 1.0 and at60["tolerance_frames"] == 20        # 9 frames is 0.15 s
    far60 = evaluate_detected_seconds(_label(tmp_path, 60.0, [100]), [125])  # 25 frames = 0.42 s
    assert far60["recall"] == 0.0 and far60["fp"] == 1


def test_seconds_scoring_reports_offsets_in_seconds_and_withholds_precision_when_partial(tmp_path):
    res = evaluate_detected_seconds(_label(tmp_path, 30.0, [60], complete=False), [54, 200])
    assert res["offsets_s"] == [-0.2]
    assert "precision" not in res and res["hit_rate"] == 1.0
