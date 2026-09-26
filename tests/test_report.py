"""Tests for the HTML report renderer."""
from synth import synth_swing_sequence
from tennis_coach.analyze import score_swing
from tennis_coach.features import extract_features
from tennis_coach.report import render_html
from tennis_coach.segment import find_swings


def test_render_html_includes_the_score_and_stroke_name():
    seq = synth_swing_sequence(n_swings=1, takeback_lateral=0.5)
    feats = extract_features(seq)
    swing = find_swings(feats)[0]
    report = score_swing(feats, swing)

    html = render_html([report])
    assert "正拍" in html
    assert f"{report.score.overall:.0f}" in html
    assert "<html" in html


def test_render_html_handles_a_swing_with_no_rubric():
    seq = synth_swing_sequence(n_swings=1, takeback_lateral=0.5)
    feats = extract_features(seq)
    swing = find_swings(feats)[0]
    report = score_swing(feats, swing)
    report.score = None
    report.stroke = "smash"

    html = render_html([report])
    assert "smash" in html
    assert "沒有這個項目的評分表" in html
