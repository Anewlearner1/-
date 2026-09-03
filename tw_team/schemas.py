"""Structured-output schemas for the team's machine-readable decisions."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TradeOrder(BaseModel):
    symbol: str = Field(description="TW ticker with exchange suffix, e.g. 2330.TW or 5274.TWO")
    action: Literal["BUY", "ADD", "TRIM", "SELL", "HOLD"]
    target_weight_pct: float = Field(
        description="Desired weight of this position as % of total equity after the trade (0-100). "
                    "SELL implies 0. HOLD means keep the current weight."
    )
    stop_loss_pct: float | None = Field(
        default=None,
        description="Stop-loss distance below entry as a percent (e.g. 10 = -10%). Null keeps the existing stop.",
    )
    thesis: str = Field(description="One or two sentences on why, in Traditional Chinese")
    time_horizon: str = Field(description="Expected holding period, e.g. '2-6 週'")
    confidence: int = Field(ge=1, le=10)


class PMDecision(BaseModel):
    market_stance: Literal["aggressive", "neutral", "defensive"]
    summary: str = Field(description="3-5 sentence summary of the decision in Traditional Chinese")
    orders: list[TradeOrder]
    risk_notes: str = Field(description="Key risks and what would make the team reverse the decision")
    goal_assessment: str = Field(description="Honest one-paragraph read of progress vs the NT$3M goal")


class RiskVerdict(BaseModel):
    approved: bool
    overall_risk_score: int = Field(ge=1, le=10, description="1 = very safe, 10 = reckless")
    max_new_exposure_pct: float = Field(
        description="The most gross exposure (% of equity) the risk manager will allow after this run"
    )
    vetoed_symbols: list[str] = Field(default_factory=list)
    required_changes: str = Field(description="Concrete adjustments the PM must respect")
    rationale: str
