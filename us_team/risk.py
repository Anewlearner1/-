"""Deterministic risk engine.

The LLM team proposes; this module disposes. Every order that reaches the
paper portfolio has passed these hard limits, so a bad model day cannot blow
the account up. The engine converts target weights into share quantities.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import RiskLimits
from .portfolio import Portfolio
from .schemas import PMDecision, RiskVerdict, TradeOrder


@dataclass
class PlannedFill:
    symbol: str
    side: str            # BUY | SELL
    shares: float
    price: float
    value: float
    stop_loss: float | None
    reason: str
    thesis: str = ""


@dataclass
class RiskReport:
    fills: list[PlannedFill] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    adjusted: list[str] = field(default_factory=list)
    halted: bool = False

    def describe(self) -> str:
        lines = []
        if self.halted:
            lines.append("- ⚠️ 回撤熔斷啟動：本輪只允許減碼，不允許新增曝險")
        for f in self.fills:
            stop = f" 停損 {f.stop_loss:.2f}" if f.stop_loss else ""
            lines.append(f"- {f.side} {f.symbol} {f.shares:g} 股 @ {f.price:.2f} = ${f.value:,.0f}{stop}｜{f.reason}")
        if not self.fills:
            lines.append("- 本輪無交易")
        for a in self.adjusted:
            lines.append(f"- 🔧 調整: {a}")
        for r in self.rejected:
            lines.append(f"- ⛔ 拒絕: {r}")
        return "\n".join(lines)


def _round_shares(shares: float) -> float:
    """Whole shares; fractional shares are not assumed."""
    return float(int(shares))


def plan_orders(decision: PMDecision, verdict: RiskVerdict | None, pf: Portfolio,
                prices: dict[str, float], limits: RiskLimits,
                universe: list[str]) -> RiskReport:
    """Turn PM target weights into concrete fills that respect the hard limits.

    Order of operations: SELL/TRIM first (frees cash), then BUY/ADD sized against
    the post-sale equity. Exposure and position-count caps are applied greedily
    in the PM's order, so the PM should list its highest-conviction buys first.
    """
    report = RiskReport()
    equity = pf.equity(prices)
    if equity <= 0:
        report.rejected.append("權益為零，無法交易")
        return report

    drawdown = pf.drawdown(prices)
    report.halted = drawdown >= limits.drawdown_halt_pct

    # Risk manager can tighten (never loosen) the exposure ceiling.
    max_exposure = limits.max_gross_exposure
    vetoed: set[str] = set()
    if verdict is not None:
        max_exposure = min(max_exposure, max(0.0, verdict.max_new_exposure_pct / 100.0))
        vetoed = {s.upper() for s in verdict.vetoed_symbols}
        if not verdict.approved:
            report.adjusted.append("風險長未核准整體方案，僅執行減碼類指令")

    cash = pf.cash
    weights = pf.weights(prices)
    invested = sum(weights.values()) * equity
    n_positions = len(pf.positions)
    trades_left = limits.max_trades_per_run

    sells = [o for o in decision.orders if o.action in ("SELL", "TRIM")]
    buys = [o for o in decision.orders if o.action in ("BUY", "ADD")]

    # ---------------- Sells ----------------
    for o in sells:
        sym = o.symbol.upper()
        pos = pf.positions.get(sym)
        if not pos:
            report.rejected.append(f"{o.action} {sym}: 無此持股")
            continue
        price = prices.get(sym)
        if not price:
            report.rejected.append(f"{o.action} {sym}: 無報價")
            continue
        if trades_left <= 0:
            report.rejected.append(f"{o.action} {sym}: 超過單輪交易上限")
            continue
        if o.action == "SELL":
            shares = pos.shares
        else:
            target_value = max(0.0, o.target_weight_pct / 100.0) * equity
            current_value = pos.shares * price
            shares = _round_shares(max(0.0, current_value - target_value) / price)
        if shares <= 0:
            report.rejected.append(f"{o.action} {sym}: 計算股數為 0")
            continue
        value = shares * price
        report.fills.append(PlannedFill(sym, "SELL", shares, price, value, None,
                                        reason=f"{o.action}: {o.thesis[:60]}"))
        cash += value
        invested -= value
        if shares >= pos.shares:
            n_positions -= 1
        trades_left -= 1

    # ---------------- Buys ----------------
    if report.halted:
        for o in buys:
            report.rejected.append(f"{o.action} {o.symbol}: 回撤 {drawdown*100:.1f}% ≥ 熔斷門檻，禁止加碼")
        return report
    if verdict is not None and not verdict.approved:
        for o in buys:
            report.rejected.append(f"{o.action} {o.symbol}: 風險長未核准")
        return report

    min_cash = limits.min_cash_pct * equity
    for o in buys:
        sym = o.symbol.upper()
        if sym in vetoed:
            report.rejected.append(f"{o.action} {sym}: 風險長否決")
            continue
        if sym not in universe:
            report.rejected.append(f"{o.action} {sym}: 不在可交易清單")
            continue
        price = prices.get(sym)
        if not price or price <= 0:
            report.rejected.append(f"{o.action} {sym}: 無報價")
            continue
        if trades_left <= 0:
            report.rejected.append(f"{o.action} {sym}: 超過單輪交易上限")
            continue
        existing = pf.positions.get(sym)
        if existing is None and n_positions >= limits.max_positions:
            report.rejected.append(f"{o.action} {sym}: 持股數已達上限 {limits.max_positions}")
            continue

        target_w = max(0.0, o.target_weight_pct / 100.0)
        if target_w > limits.max_position_pct:
            report.adjusted.append(
                f"{sym} 目標權重 {target_w*100:.0f}% 超過單一部位上限，降為 {limits.max_position_pct*100:.0f}%")
            target_w = limits.max_position_pct

        current_value = existing.shares * price if existing else 0.0
        target_value = target_w * equity
        buy_value = target_value - current_value
        if buy_value < limits.min_order_usd:
            report.rejected.append(f"{o.action} {sym}: 加碼金額 ${buy_value:,.0f} 低於最小單 ${limits.min_order_usd:,.0f}")
            continue

        # Exposure ceiling
        room_exposure = max_exposure * equity - invested
        if buy_value > room_exposure:
            if room_exposure < limits.min_order_usd:
                report.rejected.append(f"{o.action} {sym}: 總曝險已達上限 {max_exposure*100:.0f}%")
                continue
            report.adjusted.append(f"{sym} 買入金額受總曝險上限限制，${buy_value:,.0f} → ${room_exposure:,.0f}")
            buy_value = room_exposure

        # Cash floor
        room_cash = cash - min_cash
        if buy_value > room_cash:
            if room_cash < limits.min_order_usd:
                report.rejected.append(f"{o.action} {sym}: 現金不足（需保留 {limits.min_cash_pct*100:.0f}% 現金）")
                continue
            report.adjusted.append(f"{sym} 買入金額受現金下限限制，${buy_value:,.0f} → ${room_cash:,.0f}")
            buy_value = room_cash

        shares = _round_shares(buy_value / price)
        if shares <= 0:
            report.rejected.append(f"{o.action} {sym}: 價格 {price:.2f} 高於可用金額，無法買入整股")
            continue
        value = shares * price

        stop_pct = o.stop_loss_pct if o.stop_loss_pct is not None else limits.default_stop_loss_pct * 100
        stop_pct = min(max(stop_pct, 1.0), limits.max_stop_loss_pct * 100)
        stop_price = price * (1 - stop_pct / 100.0)

        report.fills.append(PlannedFill(sym, "BUY", shares, price, value, round(stop_price, 2),
                                        reason=f"{o.action} 目標 {target_w*100:.0f}%｜信心 {o.confidence}/10",
                                        thesis=o.thesis))
        cash -= value
        invested += value
        if existing is None:
            n_positions += 1
        trades_left -= 1

    return report


def apply_fills(report: RiskReport, pf: Portfolio, when=None) -> None:
    """Execute planned fills against the paper portfolio."""
    for f in report.fills:
        if f.side == "SELL":
            pf.sell(f.symbol, f.shares, f.price, reason=f.reason, when=when)
        else:
            pf.buy(f.symbol, f.shares, f.price, reason=f.reason,
                   stop_loss=f.stop_loss, thesis=f.thesis, when=when)


def describe_limits(limits: RiskLimits) -> str:
    return "\n".join([
        f"- 單一部位上限: {limits.max_position_pct*100:.0f}% 權益",
        f"- 總曝險上限: {limits.max_gross_exposure*100:.0f}%（不使用槓桿）",
        f"- 最多持股數: {limits.max_positions}",
        f"- 最低現金比例: {limits.min_cash_pct*100:.0f}%",
        f"- 預設停損: -{limits.default_stop_loss_pct*100:.0f}%，最寬 -{limits.max_stop_loss_pct*100:.0f}%",
        f"- 每輪最多交易: {limits.max_trades_per_run} 筆",
        f"- 回撤熔斷: 自峰值回撤 ≥ {limits.drawdown_halt_pct*100:.0f}% 時禁止加碼",
    ])
