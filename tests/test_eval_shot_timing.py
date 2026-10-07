"""Tests for ml/eval_shot_timing.py (pure functions; no video or pose needed)."""
import json

import pytest

from ml.eval_shot_timing import evaluate_detected, load_label, match_contacts, summarize


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
    assert "precision_within_max_tol" not in res
    assert res["hit_rate_within_5"] == 1.0


def test_complete_labels_report_precision(tmp_path):
    p = tmp_path / "clip.json"
    p.write_text(json.dumps({"video_id": "clip", "fps": 30.0, "contact_frames": [60]}))
    res = evaluate_detected(load_label(p), [58, 200])
    assert res["precision_within_max_tol"] == 0.5


def test_label_missing_a_required_key_is_rejected(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"video_id": "x", "fps": 30.0}))
    with pytest.raises(ValueError):
        load_label(p)


def test_committed_owner_labels_load_as_partial():
    from pathlib import Path
    paths = [p for p in sorted(Path("labeling/labels").glob("*.json"))
             if not p.name.endswith(".meta.json")]
    assert len(paths) == 3
    for p in paths:
        lab = load_label(p)
        assert lab["complete"] is False and lab["contact_frames"]
