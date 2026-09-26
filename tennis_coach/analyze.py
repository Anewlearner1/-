"""End-to-end scoring pipeline: video -> pose -> features -> swings -> scores.

This is the rubric half of the project ("is this good technique?"), separate
from `kinetics.py` ("how fast, and at what angle?"). They share everything up
through `find_swings` and diverge after.
"""
from __future__ import annotations

import dataclasses

from . import pose as P
from .classify import classify_stroke
from .features import Features, extract_features
from .feedback import summarize
from .metrics import SwingMetrics, extract_metrics
from .scoring import SwingScore, score_metrics
from .segment import Swing, find_swings


@dataclasses.dataclass
class SwingReport:
    swing: Swing
    stroke: str
    metrics: SwingMetrics
    score: SwingScore | None   # None when no rubric exists for `stroke`
    summary: str

    def to_dict(self) -> dict:
        return {
            "contact_frame": self.swing.contact,
            "stroke": self.stroke,
            "metrics": self.metrics.to_dict(),
            "score": self.score.to_dict() if self.score else None,
            "summary": self.summary,
        }


def analyze_video(video, *, hand: str | None = None, model_complexity: int = 2,
                  max_frames: int | None = None, progress: bool = True
                  ) -> tuple[P.PoseSequence, Features, list[SwingReport]]:
    seq = P.estimate_pose(video, model_complexity=model_complexity,
                          max_frames=max_frames, progress=progress)
    feats = extract_features(seq, hand=hand)
    swings = find_swings(feats)
    return seq, feats, [score_swing(feats, swing) for swing in swings]


def score_swing(feats: Features, swing: Swing) -> SwingReport:
    """Classify, measure and score a single already-detected swing."""
    stroke = classify_stroke(feats, swing)
    metrics = extract_metrics(feats, swing)
    try:
        score = score_metrics(metrics.to_dict(), stroke)
        summary = summarize(score)
    except ValueError:
        score = None
        summary = f"目前沒有「{stroke}」的評分表，只能提供量測數值。"
    return SwingReport(swing=swing, stroke=stroke, metrics=metrics,
                       score=score, summary=summary)
