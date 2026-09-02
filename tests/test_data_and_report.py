"""Offline tests: synthetic price history through the packet builder and report."""
import numpy as np
import pandas as pd

from us_team.data import build_packet, enrich_daily, format_packet
from us_team.report import build_markdown


def synth(n=260, start=100.0, drift=0.001, seed=0):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.02, n)
    close = start * np.cumprod(1 + rets)
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
    spy = synth(seed=1)
    row = enrich_daily(synth(seed=2, drift=0.003), "NVDA", spy)
    for key in ("ret_1m_pct", "ret_3m_pct", "sma50", "sma200", "atr_pct", "high_52w",
                "dist_52w_high_pct", "rs_vs_spy_1m", "rs_vs_spy_3m", "ann_vol_pct", "rsi"):
        assert row.get(key) is not None, key
    assert row["sector"] == "半導體"


def test_build_packet_and_format():
    history = {s: synth(seed=i) for i, s in enumerate(["SPY", "QQQ", "^VIX", "XLK", "NVDA", "AAPL"])}
    fundamentals = {"NVDA": {"symbol": "NVDA", "name": "NVIDIA", "sector": "半導體", "market_cap": 3e12,
                             "trailing_pe": 50.0, "forward_pe": 30.0, "revenue_growth": 0.6}}
    packet = build_packet(history, fundamentals, macro_symbols=["SPY", "QQQ", "^VIX"],
                          sector_symbols=["XLK"], watchlist=["NVDA", "AAPL", "MISSING"])
    assert packet.prices["NVDA"] == float(history["NVDA"]["Close"].iloc[-1])
    assert "MISSING: 無資料" in packet.errors
    text = format_packet(packet)
    assert "宏觀/指數" in text and "NVDA" in text and "3.0T" not in text  # formats as B
    assert "3000.0B" in text
    assert "資料缺失" in text


def test_build_markdown_smoke():
    result = {
        "generated_at": "2026-09-02T10:00:00", "model": "claude-opus-5", "no_trade": True,
        "goal": {"status": "on_track", "progress_pct": 0.0},
        "goal_text": "- 進度",
        "portfolio": {"equity": 30000.0, "cash": 30000.0, "cash_pct": 100.0, "gross_exposure_pct": 0.0,
                      "total_return_pct": 0.0, "drawdown_pct": 0.0, "realized_pnl": 0.0, "positions": []},
        "stop_fills": [],
        "analysts": {"macro": {"title": "宏觀策略師", "report": "Risk-on"}},
        "bull": "多", "bear": "空",
        "risk_verdict": {"approved": True, "overall_risk_score": 4, "max_new_exposure_pct": 60.0,
                         "vetoed_symbols": [], "required_changes": "無", "rationale": "ok"},
        "decision": {"market_stance": "neutral", "summary": "觀望", "risk_notes": "r", "goal_assessment": "g",
                     "orders": [{"action": "BUY", "symbol": "NVDA", "target_weight_pct": 20.0, "stop_loss_pct": 10.0,
                                 "confidence": 7, "time_horizon": "4 週", "thesis": "AI"}]},
        "risk_engine": {"text": "- 本輪無交易", "fills": [], "adjusted": [], "rejected": [], "halted": False},
        "usage": {"calls": 8, "input_tokens": 1, "output_tokens": 1, "cache_read": 0, "web_searches": 0},
        "data_errors": [],
    }
    md = build_markdown(result)
    assert "美股 AI 投資團隊報告" in md and "NVDA" in md and "--no-trade" in md
