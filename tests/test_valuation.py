import pytest

from us_team.config import ValuationAssumptions
from us_team.valuation import batch_estimate, describe_valuations, estimate_valuation

A = ValuationAssumptions(
    horizon_years=1.0, bull_growth_spread=0.15, bear_growth_spread=0.15,
    growth_floor=-0.30, growth_cap=1.50, bull_multiple_factor=1.30, bear_multiple_factor=0.60,
    default_growth_if_missing=0.10, prob_bull=0.25, prob_base=0.50, prob_bear=0.25,
)


def profitable_row(**overrides):
    row = {
        "symbol": "AAPL", "trailing_pe": 30.0, "forward_pe": 25.0,
        "earnings_growth": 0.12, "revenue_growth": 0.08,
        "market_cap": 3_000_000_000_000.0, "price_to_sales": 8.0,
    }
    row.update(overrides)
    return row


def unprofitable_row(**overrides):
    row = {
        "symbol": "FCEL", "trailing_pe": None, "forward_pe": None,
        "earnings_growth": None, "revenue_growth": -0.29,
        "market_cap": 1_150_000_000.0, "price_to_sales": 2.5,
    }
    row.update(overrides)
    return row


def test_profitable_company_uses_pe_method_and_orders_scenarios_correctly():
    est = estimate_valuation(profitable_row(), price=150.0, assumptions=A)
    assert est is not None
    assert est.method == "PE"
    assert est.bear.target_price < est.base.target_price < est.bull.target_price
    assert est.bear.growth_rate < est.base.growth_rate < est.bull.growth_rate
    # base EPS = 150/30 = 5.0; base target = 5.0 * 1.12 * 25 = 140.0
    assert est.base.target_price == pytest.approx(140.0, rel=0.01)
    assert est.base_multiple == pytest.approx(25.0)


def test_unprofitable_company_falls_back_to_ps_method():
    est = estimate_valuation(unprofitable_row(), price=14.40, assumptions=A)
    assert est is not None
    assert est.method == "PS"
    assert est.bear.target_price < est.base.target_price < est.bull.target_price
    # bear growth = -0.29 - 0.15 = -0.44, clipped to floor -0.30
    assert est.bear.growth_rate == pytest.approx(-0.30)


def test_insufficient_data_returns_none():
    row = {"symbol": "ZZZZ", "trailing_pe": None, "forward_pe": None,
          "market_cap": None, "price_to_sales": None}
    assert estimate_valuation(row, price=10.0, assumptions=A) is None
    assert estimate_valuation(profitable_row(), price=0.0, assumptions=A) is None


def test_missing_growth_falls_back_to_default_and_notes():
    row = profitable_row(earnings_growth=None, revenue_growth=None)
    est = estimate_valuation(row, price=150.0, assumptions=A)
    assert est.base_growth_rate == pytest.approx(0.10)
    assert any("預設" in n for n in est.notes)


def test_missing_earnings_growth_falls_back_to_revenue_growth():
    row = profitable_row(earnings_growth=None)
    est = estimate_valuation(row, price=150.0, assumptions=A)
    assert est.base_growth_rate == pytest.approx(0.08)
    assert any("改用營收成長率" in n for n in est.notes)


def test_growth_cap_and_floor_are_enforced():
    row = profitable_row(earnings_growth=5.0)  # absurd 500% growth
    est = estimate_valuation(row, price=150.0, assumptions=A)
    assert est.bull.growth_rate <= A.growth_cap
    assert est.base_growth_rate <= A.growth_cap


def test_probability_weighted_price_is_between_bear_and_bull():
    est = estimate_valuation(profitable_row(), price=150.0, assumptions=A)
    assert est.bear.target_price <= est.probability_weighted_price <= est.bull.target_price
    # explicit weighted-average check
    expected = (est.bull.target_price * 0.25 + est.base.target_price * 0.50 + est.bear.target_price * 0.25)
    assert est.probability_weighted_price == pytest.approx(expected, rel=0.001)


def test_negative_or_zero_trailing_pe_treated_as_unprofitable():
    row = unprofitable_row(trailing_pe=-8.0)
    est = estimate_valuation(row, price=14.40, assumptions=A)
    assert est.method == "PS"


def test_batch_estimate_skips_missing_price_and_insufficient_data():
    funds = {
        "AAPL": profitable_row(),
        "FCEL": unprofitable_row(),
        "ZZZZ": {"symbol": "ZZZZ", "trailing_pe": None, "market_cap": None, "price_to_sales": None},
        "NOPRICE": profitable_row(symbol="NOPRICE"),
    }
    prices = {"AAPL": 150.0, "FCEL": 14.40, "ZZZZ": 5.0}  # NOPRICE has no price
    out = batch_estimate(funds, prices, assumptions=A)
    assert set(out) == {"AAPL", "FCEL"}


def test_describe_valuations_formats_and_lists_skipped():
    funds = {"AAPL": profitable_row()}
    prices = {"AAPL": 150.0}
    out = batch_estimate(funds, prices, assumptions=A)
    text = describe_valuations(out, assumptions=A, skipped=["ZZZZ"])
    assert "AAPL" in text and "樂觀" in text and "保守" in text and "機率加權" in text
    assert "ZZZZ" in text


def test_describe_valuations_empty():
    text = describe_valuations({}, assumptions=A)
    assert "資料不足" in text
