from datetime import datetime

import pytest

from us_team.portfolio import Portfolio, describe_portfolio


def make(tmp_path, cash=30_000.0):
    return Portfolio.load_or_create(tmp_path / "pf.json", cash, "2026-09-01")


def test_create_and_reload_roundtrip(tmp_path):
    pf = make(tmp_path)
    pf.buy("NVDA", 10, 100.0, reason="test", stop_loss=88.0, thesis="AI")
    pf.save(tmp_path / "pf.json")

    again = Portfolio.load_or_create(tmp_path / "pf.json", 999.0)
    assert again.cash == pytest.approx(29_000.0)
    assert again.positions["NVDA"].shares == 10
    assert again.positions["NVDA"].stop_loss == 88.0
    assert again.start_date == "2026-09-01"
    assert len(again.trades) == 1


def test_buy_averages_cost_and_sell_realizes_pnl(tmp_path):
    pf = make(tmp_path)
    pf.buy("AAPL", 10, 100.0)
    pf.buy("AAPL", 10, 120.0)
    assert pf.positions["AAPL"].avg_cost == pytest.approx(110.0)
    t = pf.sell("AAPL", 5, 130.0)
    assert t.realized_pnl == pytest.approx(100.0)
    assert pf.realized_pnl == pytest.approx(100.0)
    assert pf.positions["AAPL"].shares == 15
    pf.sell("AAPL", 999, 130.0)  # clamps to remaining shares
    assert "AAPL" not in pf.positions


def test_insufficient_cash_raises(tmp_path):
    pf = make(tmp_path, cash=1_000.0)
    with pytest.raises(ValueError):
        pf.buy("MSFT", 100, 50.0)


def test_equity_exposure_weights_drawdown(tmp_path):
    pf = make(tmp_path)
    pf.buy("NVDA", 100, 100.0)
    prices = {"NVDA": 150.0}
    assert pf.equity(prices) == pytest.approx(35_000.0)
    assert pf.gross_exposure(prices) == pytest.approx(15_000 / 35_000)
    assert pf.weights(prices)["NVDA"] == pytest.approx(15_000 / 35_000)
    pf.mark(prices, when=datetime(2026, 9, 2))
    assert pf.peak_equity == pytest.approx(35_000.0)
    assert pf.drawdown({"NVDA": 75.0}) == pytest.approx(1 - 27_500 / 35_000)


def test_check_stops_sells_only_breached(tmp_path):
    pf = make(tmp_path)
    pf.buy("A", 10, 100.0, stop_loss=90.0)
    pf.buy("B", 10, 100.0, stop_loss=90.0)
    pf.buy("C", 10, 100.0)  # no stop
    fills = pf.check_stops({"A": 89.0, "B": 95.0, "C": 50.0})
    assert [f.symbol for f in fills] == ["A"]
    assert "A" not in pf.positions and "B" in pf.positions and "C" in pf.positions


def test_summary_and_describe(tmp_path):
    pf = make(tmp_path)
    pf.buy("TSLA", 10, 200.0, stop_loss=176.0, thesis="動能")
    s = pf.summary({"TSLA": 220.0})
    assert s["equity"] == pytest.approx(30_200.0)
    assert s["positions"][0]["pnl_pct"] == pytest.approx(10.0)
    text = describe_portfolio(s)
    assert "TSLA" in text and "停損" in text
