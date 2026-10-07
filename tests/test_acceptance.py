"""Logic tests for ml/acceptance.py. Detection is injected (synthetic), so these
check eligibility, pooling, thresholds and the freeze guard -- not accuracy."""
import json

import pytest

from ml import acceptance
from ml.shot_timing import ShotEvent
from tests.synth_pose import synth_stroke_sequence


def _clip(tmp_path, vid, frames, strokes, *, complete=True, numbering="opencv_decoder",
          hand="right"):
    (tmp_path / "v").mkdir(exist_ok=True)
    (tmp_path / "v" / f"{vid}.mp4").write_bytes(b"x")
    lab = {"video_id": vid, "fps": 30.0, "contact_frames": frames, "stroke_labels": strokes,
           "source": "real"}
    if hand:
        lab["racket_hand"] = hand
    p = tmp_path / f"{vid}.json"
    p.write_text(json.dumps(lab))
    (tmp_path / f"{vid}.meta.json").write_text(json.dumps(
        {"complete": complete, "frame_numbering": numbering}))
    return p


def _detect_at(frames_by_vid):
    seq = synth_stroke_sequence([(70, "forehand"), (140, "backhand")])
    def detect(video):
        return seq, [ShotEvent(f, f / 30.0, 1.0, "right") for f in frames_by_vid[video.stem]]
    return detect


def test_pooled_scores_and_pass_verdict(tmp_path):
    paths = [_clip(tmp_path, f"c{i}", [70, 140], ["forehand", "backhand"]) for i in range(3)]
    det = _detect_at({f"c{i}": [72, 139] for i in range(3)})
    r = acceptance.run_acceptance(paths, tmp_path / "v", detect=det, fps_ok=lambda v: True,
                                  min_clips=3)
    assert r["m2"] == {"tp": 6, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0}
    assert r["m3"]["n"] == 6 and r["verdict"] in ("PASS", "FAIL")
    assert r["verdict"] == ("PASS" if r["m3"]["accuracy"] >= 0.85 else "FAIL")


def test_misses_and_extras_are_counted_one_to_one(tmp_path):
    p = _clip(tmp_path, "c", [70, 140, 300], ["forehand", "backhand", "other"])
    det = _detect_at({"c": [70, 75, 400]})            # 75 duplicates 70 -> FP; 400 FP; 140, 300 missed
    r = acceptance.run_acceptance([p], tmp_path / "v", detect=det, fps_ok=lambda v: True,
                                  min_clips=1)
    assert (r["m2"]["tp"], r["m2"]["fp"], r["m2"]["fn"]) == (1, 2, 2)
    assert r["verdict"] == "FAIL" and r["m3"]["n"] == 1    # "other" never enters M3


@pytest.mark.parametrize("kw,reason", [
    ({"complete": False}, "not complete"),
    ({"numbering": "owner_player"}, "decoder"),
    ({"hand": None}, "racket_hand"),
])
def test_ineligible_labels_are_excluded_with_a_reason(tmp_path, kw, reason):
    p = _clip(tmp_path, "c", [70], ["forehand"], **kw)
    r = acceptance.run_acceptance([p], tmp_path / "v", detect=_detect_at({"c": [70]}),
                                  fps_ok=lambda v: True, min_clips=1)
    assert r["eligible_clips"] == 0 and reason in r["excluded"]["c"]
    assert r["verdict"].startswith("INSUFFICIENT")


def test_low_fps_video_is_excluded_and_too_few_clips_is_insufficient(tmp_path):
    p = _clip(tmp_path, "c", [70], ["forehand"])
    r = acceptance.run_acceptance([p], tmp_path / "v", detect=_detect_at({"c": [70]}),
                                  fps_ok=lambda v: False)
    assert "60 fps" in r["excluded"]["c"] and r["verdict"].startswith("INSUFFICIENT")


def test_changed_threshold_makes_the_run_invalid(tmp_path, monkeypatch):
    from ml import stroke_classification as sc
    monkeypatch.setattr(sc, "LATERAL_3D_FOREHAND_M", 0.25)
    p = _clip(tmp_path, "c", [70], ["forehand"])
    r = acceptance.run_acceptance([p], tmp_path / "v", detect=_detect_at({"c": [70]}),
                                  fps_ok=lambda v: True, min_clips=1)
    assert r["verdict"].startswith("INVALID") and "LATERAL_3D_FOREHAND_M" in r["config_drift"][0]


def test_frozen_config_matches_the_code_today():
    assert acceptance.config_drift() == []
