"""Score a swing's metrics against a stroke-specific rubric.

The rubric format is deliberately simple: each metric has two threshold
values, `poor_at` and `excellent_at`, and a metric's score is a linear
interpolation between them, clamped to [0, 100]. Direction (whether a higher
or lower value is better) is implicit in which threshold is bigger -- no
separate "higher_is_better" flag to keep in sync.

Calibration note: the threshold values in `rubrics/*.yaml` are placeholders
based on general coaching cues, not calibrated against a corpus of real
match footage the way an earlier version of this project's serve rubric was.
That calibration data was lost along with the project once (see README) and
has not been redone. Treat scores as directionally useful, not as a precise
measurement, until they've been checked against real clips of known quality.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import yaml

RUBRIC_DIR = Path(__file__).parent / "rubrics"


@dataclasses.dataclass
class MetricScore:
    name: str
    value: float
    score: float          # 0-100
    category: str         # "excellent" | "good" | "needs_work"
    feedback: str
    weight: float


@dataclasses.dataclass
class SwingScore:
    stroke: str
    overall: float
    metrics: list[MetricScore]

    def to_dict(self) -> dict:
        return {
            "stroke": self.stroke,
            "overall": round(self.overall, 1),
            "metrics": [
                {
                    "name": m.name,
                    "value": round(m.value, 3),
                    "score": round(m.score, 1),
                    "category": m.category,
                    "feedback": m.feedback,
                }
                for m in self.metrics
            ],
        }


def load_rubric(stroke: str) -> dict:
    path = RUBRIC_DIR / f"{stroke}.yaml"
    if not path.exists():
        raise ValueError(f"沒有「{stroke}」的評分表: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _score_one(value: float, poor_at: float, excellent_at: float) -> float:
    if excellent_at == poor_at:
        return 100.0
    t = (value - poor_at) / (excellent_at - poor_at)
    return max(0.0, min(1.0, t)) * 100.0


def _category(score: float, thresholds: dict) -> str:
    if score >= thresholds.get("excellent", 80):
        return "excellent"
    if score >= thresholds.get("good", 50):
        return "good"
    return "needs_work"


def score_metrics(metrics: dict, stroke: str, rubric: dict | None = None) -> SwingScore:
    """Score a metrics dict (e.g. `SwingMetrics.to_dict()`) against a rubric.

    Metrics present in `metrics` but not in the rubric are ignored; metrics in
    the rubric but missing from `metrics` are skipped rather than scored as 0
    -- an incomplete metric set should lower confidence, not the grade.
    """
    rubric = rubric or load_rubric(stroke)
    thresholds = rubric.get("category_thresholds", {"excellent": 80, "good": 50})

    scored: list[MetricScore] = []
    weighted_sum = 0.0
    weight_total = 0.0
    for name, spec in rubric["metrics"].items():
        if name not in metrics:
            continue
        value = float(metrics[name])
        score = _score_one(value, float(spec["poor_at"]), float(spec["excellent_at"]))
        category = _category(score, thresholds)
        feedback = spec.get("feedback", {}).get(category, "")
        weight = float(spec.get("weight", 1.0))
        scored.append(MetricScore(name=name, value=value, score=score,
                                  category=category, feedback=feedback, weight=weight))
        weighted_sum += score * weight
        weight_total += weight

    overall = weighted_sum / weight_total if weight_total > 0 else 0.0
    return SwingScore(stroke=stroke, overall=overall, metrics=scored)
