"""Goal math for the $30K → $1M in 3 years mandate.

The numbers here are deliberately blunt: the team has to know exactly how far
above market returns the target sits, so the risk manager and PM can reason
about it instead of pretending it is a normal mandate.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime

from .config import GoalConfig


@dataclass
class GoalStatus:
    start_capital: float
    target_capital: float
    horizon_years: float
    start_date: str
    as_of: str
    elapsed_years: float
    remaining_years: float
    current_equity: float
    multiple_required_total: float      # target / start
    cagr_required_at_start: float       # annualised, from day 1
    cagr_required_now: float            # annualised, from today's equity to target
    monthly_return_required_now: float
    expected_equity_on_path: float      # where a constant-CAGR path would be today
    progress_pct: float                 # (current - start) / (target - start)
    on_track_ratio: float               # current / expected_equity_on_path
    status: str                         # "ahead" | "on_track" | "behind" | "reached" | "expired"

    def to_dict(self) -> dict:
        return asdict(self)


def _years_between(a: date, b: date) -> float:
    return max(0.0, (b - a).days / 365.25)


def required_cagr(start: float, target: float, years: float) -> float:
    """Annualised growth rate needed to turn `start` into `target` in `years`."""
    if start <= 0 or target <= 0:
        raise ValueError("capital must be positive")
    if years <= 0:
        return float("inf") if target > start else 0.0
    return (target / start) ** (1.0 / years) - 1.0


def cagr_to_monthly(cagr: float) -> float:
    if cagr == float("inf"):
        return float("inf")
    return (1.0 + cagr) ** (1.0 / 12.0) - 1.0


def equity_on_path(start: float, cagr: float, elapsed_years: float) -> float:
    return start * (1.0 + cagr) ** elapsed_years


def evaluate_goal(current_equity: float, cfg: GoalConfig, start_date: str,
                  as_of: date | None = None) -> GoalStatus:
    """Compare the current equity against a constant-CAGR path to the target."""
    as_of = as_of or datetime.now().date()
    start = date.fromisoformat(start_date)
    elapsed = _years_between(start, as_of)
    remaining = max(0.0, cfg.horizon_years - elapsed)

    total_cagr = required_cagr(cfg.start_capital, cfg.target_capital, cfg.horizon_years)
    expected = equity_on_path(cfg.start_capital, total_cagr, min(elapsed, cfg.horizon_years))

    if current_equity >= cfg.target_capital:
        status = "reached"
        cagr_now = 0.0
    elif remaining <= 0:
        status = "expired"
        cagr_now = float("inf")
    else:
        cagr_now = required_cagr(max(current_equity, 1e-9), cfg.target_capital, remaining)
        ratio = current_equity / expected if expected > 0 else 0.0
        if ratio >= 1.10:
            status = "ahead"
        elif ratio >= 0.90:
            status = "on_track"
        else:
            status = "behind"

    progress = (current_equity - cfg.start_capital) / (cfg.target_capital - cfg.start_capital)

    return GoalStatus(
        start_capital=cfg.start_capital,
        target_capital=cfg.target_capital,
        horizon_years=cfg.horizon_years,
        start_date=start_date,
        as_of=as_of.isoformat(),
        elapsed_years=round(elapsed, 4),
        remaining_years=round(remaining, 4),
        current_equity=round(current_equity, 2),
        multiple_required_total=round(cfg.target_capital / cfg.start_capital, 2),
        cagr_required_at_start=round(total_cagr, 4),
        cagr_required_now=round(cagr_now, 4) if cagr_now != float("inf") else float("inf"),
        monthly_return_required_now=round(cagr_to_monthly(cagr_now), 4) if cagr_now != float("inf") else float("inf"),
        expected_equity_on_path=round(expected, 2),
        progress_pct=round(progress * 100, 2) or 0.0,
        on_track_ratio=round(current_equity / expected, 3) if expected > 0 else 0.0,
        status=status,
    )


def milestones(cfg: GoalConfig, start_date: str, step_months: int = 6) -> list[dict]:
    """Equity checkpoints along the constant-CAGR path, every `step_months`."""
    total_cagr = required_cagr(cfg.start_capital, cfg.target_capital, cfg.horizon_years)
    out = []
    months = 0
    total_months = int(round(cfg.horizon_years * 12))
    while months <= total_months:
        years = months / 12.0
        out.append({
            "month": months,
            "years": round(years, 2),
            "equity_target": round(equity_on_path(cfg.start_capital, total_cagr, years), 0),
        })
        months += step_months
    if out[-1]["month"] != total_months:
        out.append({
            "month": total_months,
            "years": round(total_months / 12.0, 2),
            "equity_target": round(cfg.target_capital, 0),
        })
    return out


def describe_goal(status: GoalStatus) -> str:
    """Human-readable (Traditional Chinese) goal summary for prompts and reports."""
    inf = status.cagr_required_now == float("inf")
    lines = [
        f"- 起始資金: ${status.start_capital:,.0f} → 目標: ${status.target_capital:,.0f} "
        f"({status.multiple_required_total:.1f}x)，期限 {status.horizon_years:.0f} 年，起算日 {status.start_date}",
        f"- 目前權益: ${status.current_equity:,.2f}｜已經過 {status.elapsed_years:.2f} 年｜剩餘 {status.remaining_years:.2f} 年",
        f"- 從第一天起所需年化報酬: {status.cagr_required_at_start*100:.1f}%",
        ("- 從現在起所需年化報酬: 已超過期限" if inf else
         f"- 從現在起所需年化報酬: {status.cagr_required_now*100:.1f}%（約每月 {status.monthly_return_required_now*100:.1f}%）"),
        f"- 等速路徑今日應有權益: ${status.expected_equity_on_path:,.0f}｜進度比 {status.on_track_ratio:.2f}｜狀態: {status.status}",
        f"- 目標達成進度: {status.progress_pct:.1f}%",
    ]
    return "\n".join(lines)
