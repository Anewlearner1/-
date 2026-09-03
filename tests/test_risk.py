import pytest

from tw_team.config import RiskLimits
from tw_team.portfolio import Portfolio, buy_fee
from tw_team.risk import apply_fills, plan_orders
from tw_team.schemas import PMDecision, RiskVerdict, TradeOrder

LIMITS = RiskLimits(
    max_position_pct=0.25, max_position_pct_otc=0.15, max_gross_exposure=1.0, max_positions=3,
    min_cash_pct=0.05, default_stop_loss_pct=0.12, max_stop_loss_pct=0.25, max_trades_per_run=4,
    drawdown_halt_pct=0.25, min_order_twd=20_000.0, price_limit_pct=0.10,
    buy_fee_rate=0.001425, sell_fee_rate=0.001425, sell_tax_rate=0.003, min_fee_twd=20.0,
    board_lot_shares=1000,
)
UNIVERSE = ["2330.TW", "2317.TW", "2454.TW", "5274.TWO", "2382.TW"]
PRICES = {"2330.TW": 100.0, "2317.TW": 50.0, "2454.TW": 200.0, "5274.TWO": 80.0, "2382.TW": 10.0}


def order(symbol, action, weight, stop=None, conf=7):
    return TradeOrder(symbol=symbol, action=action, target_weight_pct=weight, stop_loss_pct=stop,
                      thesis="測試", time_horizon="4 週", confidence=conf)


def decision(*orders):
    return PMDecision(market_stance="aggressive", summary="s", orders=list(orders), risk_notes="r", goal_assessment="g")


def verdict(approved=True, exposure=100.0, vetoed=()):
    return RiskVerdict(approved=approved, overall_risk_score=5, max_new_exposure_pct=exposure,
                       vetoed_symbols=list(vetoed), required_changes="", rationale="")


def fresh(tmp_path, cash=1_000_000.0):
    return Portfolio.load_or_create(tmp_path / "pf.json", cash, "2026-09-01")


def test_position_cap_clamps_weight_rounds_to_lot_and_sets_default_stop(tmp_path):
    pf = fresh(tmp_path)
    rep = plan_orders(decision(order("2330.TW", "BUY", 60)), verdict(), pf, PRICES, LIMITS, UNIVERSE)
    assert len(rep.fills) == 1
    f = rep.fills[0]
    assert f.value <= 0.25 * 1_000_000 + 1e-6
    assert f.shares == 2000  # 25% of 1,000,000 = 250,000 / 100 = 2500 → floored to a 1000-share lot
    assert f.shares % LIMITS.board_lot_shares == 0
    assert f.stop_loss == pytest.approx(88.0)
    assert any("單一部位上限" in a for a in rep.adjusted)


def test_otc_position_cap_is_stricter_than_listed(tmp_path):
    pf = fresh(tmp_path)
    rep = plan_orders(decision(order("5274.TWO", "BUY", 25)), verdict(), pf, PRICES, LIMITS, UNIVERSE)
    assert rep.fills[0].value <= 0.15 * 1_000_000 + 1e-6
    assert any("上櫃" in a for a in rep.adjusted)


def test_stop_loss_is_bounded(tmp_path):
    pf = fresh(tmp_path)
    rep = plan_orders(decision(order("2330.TW", "BUY", 10, stop=60)), verdict(), pf, PRICES, LIMITS, UNIVERSE)
    assert rep.fills[0].stop_loss == pytest.approx(75.0)  # capped at -25%


def test_limit_up_blocks_buy_and_limit_down_blocks_sell(tmp_path):
    pf = fresh(tmp_path)
    rep = plan_orders(decision(order("2330.TW", "BUY", 10)), verdict(), pf, PRICES, LIMITS, UNIVERSE,
                      limit_up={"2330.TW"})
    assert rep.fills == []
    assert any("漲停" in r for r in rep.rejected)

    pf.buy("2317.TW", 1000, 50.0)
    rep2 = plan_orders(decision(order("2317.TW", "SELL", 0)), verdict(), pf, PRICES, LIMITS, UNIVERSE,
                       limit_down={"2317.TW"})
    assert rep2.fills == []
    assert any("跌停" in r for r in rep2.rejected)


def test_exposure_cap_from_risk_manager_is_respected(tmp_path):
    pf = fresh(tmp_path)
    dec = decision(order("2330.TW", "BUY", 25), order("2317.TW", "BUY", 25), order("2454.TW", "BUY", 25))
    rep = plan_orders(dec, verdict(exposure=40.0), pf, PRICES, LIMITS, UNIVERSE)
    total = sum(f.value for f in rep.fills)
    assert total <= 0.40 * 1_000_000 + 1e-6
    assert [f.symbol for f in rep.fills] == ["2330.TW", "2317.TW"]  # third rejected: no room
    assert any("總曝險已達上限" in r for r in rep.rejected)


def test_vetoed_symbol_and_unknown_symbol_rejected(tmp_path):
    pf = fresh(tmp_path)
    dec = decision(order("2330.TW", "BUY", 10), order("ZZZZ.TW", "BUY", 10))
    rep = plan_orders(dec, verdict(vetoed=["2330.tw"]), pf, PRICES, LIMITS, UNIVERSE)
    assert rep.fills == []
    assert any("否決" in r for r in rep.rejected)
    assert any("不在可交易清單" in r for r in rep.rejected)


def test_max_positions_and_trade_count(tmp_path):
    pf = fresh(tmp_path)
    dec = decision(order("2330.TW", "BUY", 25), order("2317.TW", "BUY", 25), order("2454.TW", "BUY", 25),
                   order("5274.TWO", "BUY", 25))
    rep = plan_orders(dec, verdict(), pf, PRICES, LIMITS, UNIVERSE)
    assert [f.symbol for f in rep.fills] == ["2330.TW", "2317.TW", "2454.TW"]
    assert any("持股數已達上限" in r for r in rep.rejected)


def test_cash_floor_keeps_min_cash(tmp_path):
    pf = fresh(tmp_path)
    dec = decision(order("2330.TW", "BUY", 25), order("2317.TW", "BUY", 25), order("2454.TW", "BUY", 25),
                   order("5274.TWO", "BUY", 25))
    limits = RiskLimits(**{**LIMITS.__dict__, "max_positions": 10})
    rep = plan_orders(dec, verdict(), pf, PRICES, limits, UNIVERSE)
    apply_fills(rep, pf)
    assert pf.cash >= 0.05 * 1_000_000 - 1e-6


def test_drawdown_halt_blocks_buys_but_allows_sells(tmp_path):
    pf = fresh(tmp_path)
    pf.buy("2330.TW", 5000, 100.0)
    pf.mark({"2330.TW": 100.0})
    crashed = {**PRICES, "2330.TW": 40.0}
    pf.peak_equity = 2_000_000.0  # force ≥25% drawdown
    dec = decision(order("2317.TW", "BUY", 10), order("2330.TW", "SELL", 0))
    rep = plan_orders(dec, verdict(), pf, crashed, LIMITS, UNIVERSE)
    assert rep.halted
    assert [f.side for f in rep.fills] == ["SELL"]
    assert any("熔斷" in r for r in rep.rejected)


def test_trim_and_sell_sizing(tmp_path):
    pf = fresh(tmp_path)
    pf.buy("2330.TW", 6000, 100.0)
    dec = decision(order("2330.TW", "TRIM", 10))
    rep = plan_orders(dec, verdict(), pf, PRICES, LIMITS, UNIVERSE)
    assert rep.fills[0].side == "SELL"
    assert rep.fills[0].shares == 5000
    apply_fills(rep, pf)
    assert pf.positions["2330.TW"].shares == 1000


def test_unapproved_verdict_only_allows_reductions(tmp_path):
    pf = fresh(tmp_path)
    pf.buy("2330.TW", 1000, 100.0)
    dec = decision(order("2317.TW", "BUY", 10), order("2330.TW", "SELL", 0))
    rep = plan_orders(dec, verdict(approved=False), pf, PRICES, LIMITS, UNIVERSE)
    assert [f.side for f in rep.fills] == ["SELL"]
    assert any("風險長未核准" in r for r in rep.rejected)


def test_add_to_existing_position_uses_delta_rounded_to_lot(tmp_path):
    pf = fresh(tmp_path)
    pf.buy("2330.TW", 1000, 100.0)  # ~10% of equity
    dec = decision(order("2330.TW", "ADD", 30))
    rep = plan_orders(dec, verdict(), pf, PRICES, LIMITS, UNIVERSE)
    assert rep.fills[0].shares == 1000  # ~30% target - existing 100,000 ≈ +200,000 → 1 more 1000-share lot
