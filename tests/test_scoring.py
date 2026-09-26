"""Tests for the scoring math and rubric loading.

The interpolation/weighting arithmetic is checked against a tiny hand-built
rubric (not the real YAML files) so the expected numbers are exact; the real
rubric files are checked separately for basic well-formedness only, since
their thresholds are placeholders (see rubrics/*.yaml) not fixed values a
test should pin down.
"""
import pytest

from tennis_coach.scoring import load_rubric, score_metrics

TOY_RUBRIC = {
    "category_thresholds": {"excellent": 80, "good": 50},
    "metrics": {
        "a": {"weight": 1.0, "poor_at": 0.0, "excellent_at": 10.0,
              "feedback": {"excellent": "A 很好", "good": "A 普通", "needs_work": "A 不好"}},
        "b": {"weight": 3.0, "poor_at": 10.0, "excellent_at": 0.0,  # lower is better
              "feedback": {"excellent": "B 很好", "good": "B 普通", "needs_work": "B 不好"}},
    },
}


def test_a_value_at_the_excellent_threshold_scores_100():
    result = score_metrics({"a": 10.0, "b": 0.0}, "toy", rubric=TOY_RUBRIC)
    assert all(m.score == pytest.approx(100.0) for m in result.metrics)
    assert result.overall == pytest.approx(100.0)


def test_a_value_at_the_poor_threshold_scores_0():
    result = score_metrics({"a": 0.0, "b": 10.0}, "toy", rubric=TOY_RUBRIC)
    assert all(m.score == pytest.approx(0.0) for m in result.metrics)


def test_scores_are_clamped_beyond_the_thresholds():
    result = score_metrics({"a": 999.0, "b": -999.0}, "toy", rubric=TOY_RUBRIC)
    assert all(m.score == pytest.approx(100.0) for m in result.metrics)


def test_a_lower_is_better_metric_is_handled_by_threshold_direction():
    """b's poor_at (10) is bigger than its excellent_at (0), so a smaller
    value must score higher -- no separate direction flag needed."""
    result = score_metrics({"a": 0.0, "b": 2.0}, "toy", rubric=TOY_RUBRIC)
    b = next(m for m in result.metrics if m.name == "b")
    assert b.score == pytest.approx(80.0)


def test_overall_is_weighted_not_averaged_equally():
    # a=100 (weight 1), b=0 (weight 3) -> overall should be pulled toward b.
    result = score_metrics({"a": 10.0, "b": 10.0}, "toy", rubric=TOY_RUBRIC)
    assert result.overall == pytest.approx((100 * 1 + 0 * 3) / 4)


def test_a_metric_missing_from_the_input_is_skipped_not_scored_zero():
    result = score_metrics({"a": 10.0}, "toy", rubric=TOY_RUBRIC)
    assert len(result.metrics) == 1
    assert result.overall == pytest.approx(100.0)


def test_category_boundaries_match_the_configured_thresholds():
    result = score_metrics({"a": 8.0, "b": 5.0}, "toy", rubric=TOY_RUBRIC)
    a = next(m for m in result.metrics if m.name == "a")
    assert a.score == pytest.approx(80.0)
    assert a.category == "excellent"


def test_feedback_text_comes_from_the_matched_category():
    result = score_metrics({"a": 0.0, "b": 10.0}, "toy", rubric=TOY_RUBRIC)
    assert all(m.feedback.endswith("不好") for m in result.metrics)


def test_an_unknown_stroke_without_a_rubric_file_raises():
    with pytest.raises(ValueError):
        load_rubric("smash-that-does-not-exist")


@pytest.mark.parametrize("stroke", ["forehand", "backhand", "serve"])
def test_the_real_rubric_files_load_and_are_well_formed(stroke):
    rubric = load_rubric(stroke)
    assert rubric["metrics"], f"{stroke}.yaml 沒有任何指標"
    for name, spec in rubric["metrics"].items():
        assert "poor_at" in spec and "excellent_at" in spec, name
        assert spec["poor_at"] != spec["excellent_at"], name
        assert set(spec.get("feedback", {})) >= {"excellent", "good", "needs_work"}, name
