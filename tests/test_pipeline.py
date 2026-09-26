"""Integration tests for the scoring pipeline (analyze.py) end to end."""
import pytest

from synth import synth_swing_sequence
from tennis_coach.analyze import analyze_video, score_swing
from tennis_coach.features import extract_features
from tennis_coach.segment import find_swings


def test_score_swing_produces_a_stroke_metrics_and_score(monkeypatch):
    seq = synth_swing_sequence(n_swings=1, takeback_lateral=0.5, overhead=False)
    feats = extract_features(seq)
    swing = find_swings(feats)[0]

    report = score_swing(feats, swing)
    assert report.stroke == "forehand"
    assert report.score is not None
    assert 0.0 <= report.score.overall <= 100.0
    assert report.summary


def test_report_to_dict_is_json_shaped(monkeypatch):
    seq = synth_swing_sequence(n_swings=1, takeback_lateral=0.5)
    feats = extract_features(seq)
    swing = find_swings(feats)[0]
    d = score_swing(feats, swing).to_dict()

    assert set(d) == {"contact_frame", "stroke", "metrics", "score", "summary"}
    assert d["score"]["stroke"] == "forehand"
    assert isinstance(d["score"]["metrics"], list)


def test_analyze_video_runs_end_to_end_with_a_stubbed_pose(monkeypatch):
    synthetic = synth_swing_sequence(n_swings=2, takeback_lateral=0.5)
    monkeypatch.setattr("tennis_coach.pose.estimate_pose", lambda video, **kw: synthetic)

    seq, feats, reports = analyze_video("fake.mp4", progress=False)
    assert len(reports) == 2
    assert all(r.stroke == "forehand" for r in reports)
    assert all(r.score is not None for r in reports)


def test_a_stroke_without_a_rubric_still_reports_metrics_not_a_crash(monkeypatch):
    """classify_stroke can only return forehand/backhand/serve today, all of
    which have a rubric -- but score_swing must degrade gracefully rather than
    raising if that ever changes (e.g. a smash added to classify.py later)."""
    from tennis_coach import analyze as analyze_mod

    seq = synth_swing_sequence(n_swings=1, takeback_lateral=0.5)
    feats = extract_features(seq)
    swing = find_swings(feats)[0]

    monkeypatch.setattr(analyze_mod, "classify_stroke", lambda f, s: "smash")
    report = analyze_mod.score_swing(feats, swing)
    assert report.stroke == "smash"
    assert report.score is None
    assert "smash" in report.summary or report.summary
