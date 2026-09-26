"""Turn a scored swing into a short, actionable summary in natural language."""
from __future__ import annotations

from .scoring import SwingScore

STROKE_ZH = {"forehand": "正拍", "backhand": "反拍", "serve": "發球"}


def summarize(score: SwingScore, top_n: int = 2) -> str:
    """One paragraph: what's working, what to fix first.

    Priority for "what to fix" is by rubric weight, not by how low the score
    is -- a badly-scored but low-weight metric matters less than a
    moderately-scored high-weight one.
    """
    needs_work = sorted(
        (m for m in score.metrics if m.category == "needs_work"),
        key=lambda m: m.weight, reverse=True,
    )[:top_n]
    strong = [m for m in score.metrics if m.category == "excellent"]

    stroke_zh = STROKE_ZH.get(score.stroke, score.stroke)
    parts = [f"這次{stroke_zh}整體評分 {score.overall:.0f} 分。"]

    if needs_work:
        parts.append("最值得優先調整：" + "；".join(m.feedback for m in needs_work if m.feedback))
    if strong:
        parts.append("做得好的地方：" + "；".join(m.feedback for m in strong[:2] if m.feedback))
    if not needs_work and not strong:
        parts.append("各項指標都落在中間水準，沒有特別突出的優缺點。")

    return " ".join(parts)
