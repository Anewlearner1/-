from datetime import datetime

import pytest

from tw_team.portfolio import Portfolio, buy_fee, describe_portfolio, sell_fee, sell_tax


def make(tmp_path, cash=1_000_000.0):
    return Portfolio.load_or_create(tmp_path / "pf.json", cash, "2026-09-01")


def test_create_and_reload_roundtrip(tmp_path):
    pf = make(tmp_path)
    pf.buy("2330.TW", 1000, 100.0, reason="test", stop_loss=88.0, thesis="半導體")
    pf.save(tmp_path / "pf.json")

    again = Portfolio.load_or_create(tmp_path / "pf.json", 999.0)
    expected_cash = 1_000_000.0 - (1000 * 100.0 + buy_fee(1000 * 100.0))
    assert again.cash == pytest.approx(expected_cash)
    assert again.positions["2330.TW"].shares == 1000
    assert again.positions["2330.TW"].stop_loss == 88.0
    assert again.start_date == "2026-09-01"
    assert len(again.trades) == 1


def test_buy_includes_fee_in_cost_basis(tmp_path):
    pf = make(tmp_path)
    pf.buy("2330.TW", 1000, 100.0)
    total_cost = 1000 * 100.0 + buy_fee(1000 * 100.0)
    assert pf.positions["2330.TW"].avg_cost == pytest.approx(total_cost / 1000)


def test_buy_averages_cost_and_sell_realizes_pnl(tmp_path):
    pf = make(tmp_path)
    pf.buy("2317.TW", 1000, 100.0)
    pf.buy("2317.TW", 1000, 120.0)
    cost1 = 1000 * 100.0 + buy_fee(1000 * 100.0)
    cost2 = 1000 * 120.0 + buy_fee(1000 * 120.0)
    assert pf.positions["2317.TW"].avg_cost == pytest.approx((cost1 + cost2) / 2000)

    t = pf.sell("2317.TW", 500, 130.0)
    value = 500 * 130.0
    net_proceeds = value - sell_fee(value) - sell_tax(value)
    expected_pnl = net_proceeds - pf.positions["2317.TW"].avg_cost * 500
    assert t.realized_pnl == pytest.approx(expected_pnl)
    assert pf.positions["2317.TW"].shares == 1500

    pf.sell("2317.TW", 999_999, 130.0)  # clamps to remaining shares
    assert "2317.TW" not in pf.positions


def test_insufficient_cash_raises(tmp_path):
    pf = make(tmp_path, cash=50_000.0)
    with pytest.raises(ValueError):
        pf.buy("2454.TW", 1000, 100.0)


def test_equity_exposure_weights_drawdown(tmp_path):
    pf = make(tmp_path)
    pf.buy("2330.TW", 5000, 100.0)
    prices = {"2330.TW": 150.0}
    invested_cost = 5000 * 100.0 + buy_fee(5000 * 100.0)
    equity = (1_000_000 - invested_cost) + 5000 * 150.0
    assert pf.equity(prices) == pytest.approx(equity)
    assert pf.gross_exposure(prices) == pytest.approx(5000 * 150.0 / equity)
    pf.mark(prices, when=datetime(2026, 9, 2))
    assert pf.peak_equity == pytest.approx(equity)
    lower = pf.equity({"2330.TW": 75.0})
    assert pf.drawdown({"2330.TW": 75.0}) == pytest.approx(1 - lower / equity)


def test_check_stops_sells_only_breached_and_skips_limit_down(tmp_path):
    pf = make(tmp_path)
    pf.buy("A.TW", 1000, 100.0, stop_loss=90.0)
    pf.buy("B.TW", 1000, 100.0, stop_loss=90.0)
    pf.buy("C.TW", 1000, 100.0)  # no stop
    pf.buy("D.TWO", 1000, 100.0, stop_loss=90.0)
    fills = pf.check_stops({"A.TW": 89.0, "B.TW": 95.0, "C.TW": 50.0, "D.TWO": 89.0},
                           limit_down={"D.TWO"})
    assert [f.symbol for f in fills] == ["A.TW"]
    assert "A.TW" not in pf.positions
    assert "B.TW" in pf.positions and "C.TW" in pf.positions
    assert "D.TWO" in pf.positions  # stop skipped: locked at limit-down


def test_summary_and_describe(tmp_path):
    pf = make(tmp_path)
    pf.buy("2330.TW", 1000, 200.0, stop_loss=176.0, thesis="動能")
    s = pf.summary({"2330.TW": 220.0})
    assert s["positions"][0]["lots"] == pytest.approx(1.0)
    assert s["positions"][0]["pnl_pct"] == pytest.approx((220.0 / s["positions"][0]["avg_cost"] - 1) * 100, abs=0.01)
    text = describe_portfolio(s)
    assert "2330.TW" in text and "停損" in text and "張" in text
