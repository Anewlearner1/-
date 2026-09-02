import pytest

from us_team.config import RiskLimits
from us_team.portfolio import Portfolio
from us_team.risk import apply_fills, plan_orders
from us_team.schemas import PMDecision, RiskVerdict, TradeOrder

LIMITS = RiskLimits(
    max_position_pct=0.25, max_gross_exposure=1.0, max_positions=3, min_cash_pct=0.05,
    default_stop_loss_pct=0.12, max_stop_loss_pct=0.25, max_trades_per_run=4,
    drawdown_halt_pct=0.25, min_order_usd=200.0,
)
UNIVERSE = ["NVDA", "AAPL", "TSLA", "META", "AMD"]
PRICES = {"NVDA": 100.0, "AAPL": 50.0, "TSLA": 200.0, "META": 500.0, "AMD": 10.0}


def order(symbol, action, weight, stop=None, conf=7):
    return TradeOrder(symbol=symbol, action=action, target_weight_pct=weight, stop_loss_pct=stop,
                      thesis="測試", time_horizon="4 週", confidence=conf)


def decision(*orders):
    return PMDecision(market_stance="aggressive", summary="s", orders=list(orders), risk_notes="r", goal_assessment="g")


def verdict(approved=True, exposure=100.0, vetoed=()):
    return RiskVerdict(approved=approved, overall_risk_score=5, max_new_exposure_pct=exposure,
                       vetoed_symbols=list(vetoed), required_changes="", rationale="")


def fresh(tmp_path, cash=30_000.0):
    return Portfolio.load_or_create(tmp_path / "pf.json", cash, "2026-09-01")


def test_position_cap_clamps_weight_and_sets_default_stop(tmp_path):
    pf = fresh(tmp_path)
    rep = plan_orders(decision(order("NVDA", "BUY", 60)), verdict(), pf, PRICES, LIMITS, UNIVERSE)
    assert len(rep.fills) == 1
    f = rep.fills[0]
    assert f.value <= 0.25 * 30_000 + 1e-6
    assert f.shares == 75
    assert f.stop_loss == pytest.approx(88.0)
    assert any("單一部位上限" in a for a in rep.adjusted)


def test_stop_loss_is_bounded(tmp_path):
    pf = fresh(tmp_path)
    rep = plan_orders(decision(order("NVDA", "BUY", 10, stop=60)), verdict(), pf, PRICES, LIMITS, UNIVERSE)
    assert rep.fills[0].stop_loss == pytest.approx(75.0)  # capped at -25%


def test_exposure_cap_from_risk_manager_is_respected(tmp_path):
    pf = fresh(tmp_path)
    dec = decision(order("NVDA", "BUY", 25), order("AAPL", "BUY", 25), order("TSLA", "BUY", 25))
    rep = plan_orders(dec, verdict(exposure=40.0), pf, PRICES, LIMITS, UNIVERSE)
    total = sum(f.value for f in rep.fills)
    assert total <= 0.40 * 30_000 + 1e-6
    assert [f.symbol for f in rep.fills] == ["NVDA", "AAPL"]  # third rejected: no room
    assert any("總曝險已達上限" in r for r in rep.rejected)


def test_vetoed_symbol_and_unknown_symbol_rejected(tmp_path):
    pf = fresh(tmp_path)
    dec = decision(order("NVDA", "BUY", 10), order("ZZZZ", "BUY", 10))
    rep = plan_orders(dec, verdict(vetoed=["nvda"]), pf, PRICES, LIMITS, UNIVERSE)
    assert rep.fills == []
    assert any("否決" in r for r in rep.rejected)
    assert any("不在可交易清單" in r for r in rep.rejected)


def test_max_positions_and_trade_count(tmp_path):
    pf = fresh(tmp_path)
    dec = decision(order("NVDA", "BUY", 10), order("AAPL", "BUY", 10), order("TSLA", "BUY", 10),
                   order("META", "BUY", 10))
    rep = plan_orders(dec, verdict(), pf, PRICES, LIMITS, UNIVERSE)
    assert [f.symbol for f in rep.fills] == ["NVDA", "AAPL", "TSLA"]
    assert any("持股數已達上限" in r for r in rep.rejected)


def test_cash_floor_keeps_min_cash(tmp_path):
    pf = fresh(tmp_path)
    dec = decision(order("NVDA", "BUY", 25), order("AAPL", "BUY", 25), order("TSLA", "BUY", 25),
                   order("META", "BUY", 25))
    limits = RiskLimits(**{**LIMITS.__dict__, "max_positions": 10})
    rep = plan_orders(dec, verdict(), pf, PRICES, limits, UNIVERSE)
    apply_fills(rep, pf)
    assert pf.cash >= 0.05 * 30_000 - 1e-6


def test_drawdown_halt_blocks_buys_but_allows_sells(tmp_path):
    pf = fresh(tmp_path)
    pf.buy("NVDA", 100, 100.0)
    pf.mark({"NVDA": 100.0})
    crashed = {**PRICES, "NVDA": 40.0}  # equity 26k vs peak 30k → 13%? make it worse
    pf.peak_equity = 50_000.0            # force ≥25% drawdown
    dec = decision(order("AAPL", "BUY", 10), order("NVDA", "SELL", 0))
    rep = plan_orders(dec, verdict(), pf, crashed, LIMITS, UNIVERSE)
    assert rep.halted
    assert [f.side for f in rep.fills] == ["SELL"]
    assert any("熔斷" in r for r in rep.rejected)


def test_trim_and_sell_sizing(tmp_path):
    pf = fresh(tmp_path)
    pf.buy("NVDA", 60, 100.0)  # 6000 of 30000 = 20%
    dec = decision(order("NVDA", "TRIM", 10))
    rep = plan_orders(dec, verdict(), pf, PRICES, LIMITS, UNIVERSE)
    assert rep.fills[0].side == "SELL"
    assert rep.fills[0].shares == 30
    apply_fills(rep, pf)
    assert pf.positions["NVDA"].shares == 30


def test_unapproved_verdict_only_allows_reductions(tmp_path):
    pf = fresh(tmp_path)
    pf.buy("NVDA", 10, 100.0)
    dec = decision(order("AAPL", "BUY", 10), order("NVDA", "SELL", 0))
    rep = plan_orders(dec, verdict(approved=False), pf, PRICES, LIMITS, UNIVERSE)
    assert [f.side for f in rep.fills] == ["SELL"]
    assert any("風險長未核准" in r for r in rep.rejected)


def test_add_to_existing_position_uses_delta(tmp_path):
    pf = fresh(tmp_path)
    pf.buy("NVDA", 30, 100.0)  # 10%
    dec = decision(order("NVDA", "ADD", 20))
    rep = plan_orders(dec, verdict(), pf, PRICES, LIMITS, UNIVERSE)
    assert rep.fills[0].shares == 30  # 20% of 30k = 6000 → +3000 → 30 shares
