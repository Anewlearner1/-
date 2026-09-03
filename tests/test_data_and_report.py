"""Offline tests: synthetic price history through the packet builder and report."""
import numpy as np
import pandas as pd

from tw_team.data import build_packet, enrich_daily, format_packet
from tw_team.report import build_markdown


def synth(n=260, start=100.0, drift=0.001, seed=0, spike_last=None):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.02, n)
    close = start * np.cumprod(1 + rets)
    if spike_last is not None:
        close[-1] = close[-2] * (1 + spike_last)
    idx = pd.bdate_range(end="2026-09-01", periods=n)
    df = pd.DataFrame({
        "Open": close * (1 + rng.normal(0, 0.003, n)),
        "High": close * (1 + abs(rng.normal(0, 0.01, n))),
        "Low": close * (1 - abs(rng.normal(0, 0.01, n))),
        "Close": close,
        "Volume": rng.integers(1_000_000, 5_000_000, n),
    }, index=idx)
    return df


def test_enrich_daily_has_swing_features():
    taiex = synth(seed=1)
    row = enrich_daily(synth(seed=2, drift=0.003), "2330.TW", taiex)
    for key in ("ret_1m_pct", "ret_3m_pct", "sma50", "sma200", "atr_pct", "high_52w",
                "dist_52w_high_pct", "rs_vs_taiex_1m", "rs_vs_taiex_3m", "ann_vol_pct", "rsi"):
        assert row.get(key) is not None, key
    assert row["sector"] == "半導體"
    assert row["is_otc"] is False


def test_enrich_daily_flags_otc_and_limit_moves():
    row_otc = enrich_daily(synth(seed=3), "5274.TWO")
    assert row_otc["is_otc"] is True

    up = enrich_daily(synth(seed=4, spike_last=0.10), "2330.TW")
    assert up["limit_up"] is True
    down = enrich_daily(synth(seed=5, spike_last=-0.10), "2330.TW")
    assert down["limit_down"] is True


def test_build_packet_and_format():
    history = {s: synth(seed=i) for i, s in enumerate(["^TWII", "^TWOII", "0050.TW", "2330.TW", "2317.TW"])}
    fundamentals = {"2330.TW": {"symbol": "2330.TW", "name": "台積電", "sector": "半導體", "market_cap": 5e11,
                                "trailing_pe": 25.0, "forward_pe": 20.0, "revenue_growth": 0.3}}
    chips = {"2330.TW": {"symbol": "2330.TW", "date": "2026-09-01", "foreign_net_lots": 1234.0,
                        "trust_net_lots": 56.0, "dealer_net_lots": -12.0, "total_net_lots": 1278.0,
                        "margin_balance_lots": 5000.0, "margin_balance_chg_lots": -30.0,
                        "short_balance_lots": 200.0, "short_balance_chg_lots": 5.0},
            "2317.TW": {"symbol": "2317.TW", "error": "查無籌碼資料"}}
    packet = build_packet(history, fundamentals, chips, macro_symbols=["^TWII", "^TWOII"],
                          sector_symbols=["0050.TW"], watchlist=["2330.TW", "2317.TW", "MISSING.TW"])
    assert packet.prices["2330.TW"] == float(history["2330.TW"]["Close"].iloc[-1])
    assert "MISSING.TW: 無資料" in packet.errors
    text = format_packet(packet)
    assert "宏觀/指數" in text and "2330.TW" in text
    assert "5000.0億" in text
    assert "籌碼面" in text and "外資買賣超 1234.00 張" in text
    assert "籌碼資料缺失" in text
    assert "資料缺失" in text


def test_build_markdown_smoke():
    result = {
        "generated_at": "2026-09-02T10:00:00", "model": "claude-opus-5", "no_trade": True,
        "goal": {"status": "on_track", "progress_pct": 0.0},
        "goal_text": "- 進度",
        "portfolio": {"equity": 1_000_000.0, "cash": 1_000_000.0, "cash_pct": 100.0, "gross_exposure_pct": 0.0,
                      "total_return_pct": 0.0, "drawdown_pct": 0.0, "realized_pnl": 0.0, "positions": []},
        "stop_fills": [], "skipped_stops": [],
        "analysts": {"macro": {"title": "宏觀策略師", "report": "Risk-on"}},
        "bull": "多", "bear": "空",
        "risk_verdict": {"approved": True, "overall_risk_score": 4, "max_new_exposure_pct": 60.0,
                         "vetoed_symbols": [], "required_changes": "無", "rationale": "ok"},
        "decision": {"market_stance": "neutral", "summary": "觀望", "risk_notes": "r", "goal_assessment": "g",
                     "orders": [{"action": "BUY", "symbol": "2330.TW", "target_weight_pct": 20.0, "stop_loss_pct": 10.0,
                                 "confidence": 7, "time_horizon": "4 週", "thesis": "半導體"}]},
        "risk_engine": {"text": "- 本輪無交易", "fills": [], "adjusted": [], "rejected": [], "halted": False},
        "usage": {"calls": 9, "input_tokens": 1, "output_tokens": 1, "cache_read": 0, "web_searches": 0},
        "data_errors": [],
    }
    md = build_markdown(result)
    assert "台股 AI 投資團隊報告" in md and "2330.TW" in md and "--no-trade" in md
