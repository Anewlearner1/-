"""Deterministic three-scenario valuation model (optimistic / neutral / conservative).

This turns the fundamentals `data.fetch_fundamentals` already pulls into a
bull/base/bear one-year price target, using earnings-multiple projection for
profitable names and revenue-multiple projection for unprofitable/early-stage
ones — the same split an analyst has to make by eye. It is a fast heuristic,
not a first-principles DCF: the point is that every role (analysts, bull,
bear, PM) references the same code-computed numbers instead of the LLM
inventing a target price each run. All assumptions live in
`config.ValuationAssumptions` and are overridable via environment variables.

    profitable (trailing P/E > 0):
        EPS_ttm = price / trailing_pe
        target  = EPS_ttm * (1 + growth) ** horizon * exit_pe

    unprofitable (no usable P/E, but price/sales available):
        revenue_ttm = market_cap / price_to_sales
        target      = revenue_ttm * (1 + growth) ** horizon * exit_ps / shares_outstanding

Bull/bear growth is the base growth rate plus/minus a spread in percentage
points (not a multiplier), so a company with negative base growth still gets
worse, not better, in the bear case. Bull/bear multiples scale the base
multiple by a factor, since multiples are always positive.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .config import VALUATION, ValuationAssumptions


@dataclass
class ScenarioResult:
    label: str            # "樂觀" | "中立" | "保守"
    growth_rate: float
    exit_multiple: float
    target_price: float
    implied_return_pct: float


@dataclass
class ValuationEstimate:
    symbol: str
    method: str            # "PE" | "PS"
    current_price: float
    base_growth_rate: float
    base_multiple: float
    bull: ScenarioResult
    base: ScenarioResult
    bear: ScenarioResult
    probability_weighted_price: float
    probability_weighted_return_pct: float
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _clip_growth(g: float, a: ValuationAssumptions) -> float:
    return max(a.growth_floor, min(a.growth_cap, g))


def _project(base_value: float, growth: float, multiple: float, horizon: float) -> float:
    """`base_value` (EPS or revenue, TTM) compounded at `growth` for `horizon` years, times exit multiple."""
    return base_value * (1.0 + growth) ** horizon * multiple


def estimate_valuation(fundamentals: dict, price: float,
                       assumptions: ValuationAssumptions | None = None) -> ValuationEstimate | None:
    """Build a bull/base/bear price target for one symbol.

    `fundamentals` is one row from `data.fetch_fundamentals` (same keys).
    Returns None when there isn't enough data to value the stock either way
    (no usable P/E and no usable P/S) — callers should skip, not guess.
    """
    a = assumptions or VALUATION
    if not price or price <= 0:
        return None

    notes: list[str] = []
    trailing_pe = fundamentals.get("trailing_pe")
    forward_pe = fundamentals.get("forward_pe")
    market_cap = fundamentals.get("market_cap")
    price_to_sales = fundamentals.get("price_to_sales")
    earnings_growth = fundamentals.get("earnings_growth")
    revenue_growth = fundamentals.get("revenue_growth")

    profitable = trailing_pe is not None and trailing_pe > 0
    if profitable:
        method = "PE"
        base_value = price / trailing_pe                 # EPS_ttm
        base_multiple = forward_pe if (forward_pe and forward_pe > 0) else trailing_pe
        if earnings_growth is not None:
            base_growth = earnings_growth
        elif revenue_growth is not None:
            base_growth = revenue_growth
            notes.append("缺獲利成長率，改用營收成長率估算")
        else:
            base_growth = a.default_growth_if_missing
            notes.append(f"缺成長率資料，預設 {a.default_growth_if_missing*100:.0f}%")
    elif price_to_sales and price_to_sales > 0 and market_cap:
        method = "PS"
        base_value = market_cap / price_to_sales          # revenue_ttm (implied)
        base_multiple = price_to_sales
        if revenue_growth is not None:
            base_growth = revenue_growth
        else:
            base_growth = a.default_growth_if_missing
            notes.append(f"缺營收成長率資料，預設 {a.default_growth_if_missing*100:.0f}%")
    else:
        return None  # not enough data either way

    base_growth = _clip_growth(base_growth, a)
    bull_growth = _clip_growth(base_growth + a.bull_growth_spread, a)
    bear_growth = _clip_growth(base_growth - a.bear_growth_spread, a)

    shares_outstanding = (market_cap / price) if (method == "PS" and market_cap) else None

    def to_price(projected_value: float) -> float:
        return projected_value / shares_outstanding if method == "PS" else projected_value

    def scenario(label: str, growth: float, multiple: float) -> ScenarioResult:
        target = to_price(_project(base_value, growth, multiple, a.horizon_years))
        return ScenarioResult(
            label=label, growth_rate=round(growth, 4), exit_multiple=round(multiple, 2),
            target_price=round(target, 2),
            implied_return_pct=round((target / price - 1) * 100, 1),
        )

    bull = scenario("樂觀", bull_growth, base_multiple * a.bull_multiple_factor)
    base = scenario("中立", base_growth, base_multiple)
    bear = scenario("保守", bear_growth, base_multiple * a.bear_multiple_factor)

    prob_sum = a.prob_bull + a.prob_base + a.prob_bear
    weighted_price = (
        bull.target_price * a.prob_bull + base.target_price * a.prob_base + bear.target_price * a.prob_bear
    ) / prob_sum

    return ValuationEstimate(
        symbol=fundamentals.get("symbol", ""), method=method, current_price=round(price, 2),
        base_growth_rate=round(base_growth, 4), base_multiple=round(base_multiple, 2),
        bull=bull, base=base, bear=bear,
        probability_weighted_price=round(weighted_price, 2),
        probability_weighted_return_pct=round((weighted_price / price - 1) * 100, 1),
        notes=notes,
    )


def batch_estimate(fundamentals_by_symbol: dict[str, dict], prices: dict[str, float],
                   assumptions: ValuationAssumptions | None = None) -> dict[str, ValuationEstimate]:
    """Estimate every symbol that has both a price and fundamentals. Skips the rest silently —
    callers can diff against `fundamentals_by_symbol` keys to see what was skipped."""
    out: dict[str, ValuationEstimate] = {}
    for sym, f in fundamentals_by_symbol.items():
        price = prices.get(sym)
        if price is None:
            continue
        est = estimate_valuation(f, price, assumptions)
        if est is not None:
            out[sym] = est
    return out


# --------------------------------------------------------------------------- #
# Prompt / report formatting
# --------------------------------------------------------------------------- #
def describe_valuation(v: ValuationEstimate) -> str:
    method_label = "本益比法 (P/E)" if v.method == "PE" else "虧損無法用本益比，改用市銷率法 (P/S)"
    line = (
        f"- {v.symbol}: 現價 {v.current_price}｜{method_label}｜基準成長 {v.base_growth_rate*100:.0f}%"
        f"｜基準倍數 {v.base_multiple:.1f}x\n"
        f"    樂觀 ${v.bull.target_price} ({v.bull.implied_return_pct:+.0f}%，成長 {v.bull.growth_rate*100:.0f}%"
        f"／倍數 {v.bull.exit_multiple:.1f}x)｜"
        f"中立 ${v.base.target_price} ({v.base.implied_return_pct:+.0f}%)｜"
        f"保守 ${v.bear.target_price} ({v.bear.implied_return_pct:+.0f}%，成長 {v.bear.growth_rate*100:.0f}%"
        f"／倍數 {v.bear.exit_multiple:.1f}x)\n"
        f"    機率加權目標價 ${v.probability_weighted_price} ({v.probability_weighted_return_pct:+.0f}%)"
    )
    if v.notes:
        line += "\n    註: " + "；".join(v.notes)
    return line


def describe_valuations(estimates: dict[str, ValuationEstimate],
                        assumptions: ValuationAssumptions | None = None,
                        skipped: list[str] | None = None) -> str:
    a = assumptions or VALUATION
    header = (
        f"三情境估值模型（{a.horizon_years:.0f} 年期目標價，機率權重 樂觀 {a.prob_bull*100:.0f}% / "
        f"中立 {a.prob_base*100:.0f}% / 保守 {a.prob_bear*100:.0f}%）。"
        f"樂觀＝基準成長 +{a.bull_growth_spread*100:.0f}pp、倍數 ×{a.bull_multiple_factor:.1f}；"
        f"保守＝基準成長 -{a.bear_growth_spread*100:.0f}pp、倍數 ×{a.bear_multiple_factor:.1f}。"
        "這是程式算出的機械式估值，不是預測，僅供分析師與 PM 交叉參考，不能取代對商業模式的判斷。"
    )
    if not estimates:
        return header + "\n(資料不足，本輪無標的可估算)"
    lines = [header, ""]
    lines += [describe_valuation(v) for v in estimates.values()]
    if skipped:
        lines += ["", f"資料不足無法估算: {', '.join(skipped)}"]
    return "\n".join(lines)
