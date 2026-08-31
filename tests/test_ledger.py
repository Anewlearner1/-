"""
帳本與結算的規格 —— 這些測試就是 ledger.py 的定義。

結算必須是純函數：同樣的 Call 加同樣的價格序列，永遠得到同樣的結果。
不得有 LLM 參與，否則計分會和被計分的東西一樣不可靠。
"""
import datetime as dt
import pandas as pd
import pytest

import ledger


def bars(rows):
    """[(日期, 高, 低, 收)] → 結算用的日 K。"""
    idx = [dt.date(2026, 9, 1) + dt.timedelta(days=i) for i in range(len(rows))]
    return pd.DataFrame(
        [{"High": h, "Low": lo, "Close": c} for h, lo, c in rows],
        index=pd.to_datetime(idx),
    )


# --------------------------------------------------------------------------
# 時間框架解析：模型寫的是自由文字，帳本需要天數
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,lo,hi", [
    ("3-6 個月", 90, 190),
    ("2-4 週（事件不確定性消化期）", 14, 30),
    ("6-12 個月", 180, 370),
    ("3年以上", 1000, 1200),
    ("1-2 週", 7, 15),
    ("3-6個月（至檢調案釐清）", 90, 190),
])
def test_horizon_parsing(text, lo, hi):
    days = ledger.parse_horizon_days(text)
    assert lo <= days <= hi, f"{text!r} → {days} 天，期望落在 {lo}-{hi}"


def test_horizon_unparseable_falls_back():
    """看不懂就給預設值並標記，不可以拋例外讓整場會議寫不進去。"""
    assert ledger.parse_horizon_days("看情況") == ledger.DEFAULT_HORIZON_DAYS


# --------------------------------------------------------------------------
# R 倍數：核心單位。R = (出場 - 進場) / (進場 - 停損)
# --------------------------------------------------------------------------

def test_buy_hits_target_gives_positive_r():
    """進場100 停損90（風險10）目標130 → 打到目標 = +3R。"""
    r = ledger.resolve_call(
        {"stance": "BUY", "entry": 100, "stop": 90, "target": 130},
        bars([(105, 98, 100), (132, 120, 130)]),
    )
    assert r["outcome"] == "TARGET_HIT"
    assert r["r_multiple"] == pytest.approx(3.0)


def test_buy_hits_stop_gives_minus_one_r():
    """打到停損永遠是 -1R —— 這是 R 這個單位的定義。"""
    r = ledger.resolve_call(
        {"stance": "BUY", "entry": 100, "stop": 90, "target": 130},
        bars([(105, 89, 92)]),
    )
    assert r["outcome"] == "STOP_HIT"
    assert r["r_multiple"] == pytest.approx(-1.0)


def test_same_day_both_hit_assumes_stop_first():
    """日K看不出盤中順序 —— 保守假設停損先到，寧可低估自己。"""
    r = ledger.resolve_call(
        {"stance": "BUY", "entry": 100, "stop": 90, "target": 130},
        bars([(135, 88, 120)]),
    )
    assert r["outcome"] == "STOP_HIT"
    assert r["r_multiple"] == pytest.approx(-1.0)


def test_expired_without_touching_uses_last_close():
    """到期沒碰到任何一邊 → 用最後收盤價算 R。"""
    r = ledger.resolve_call(
        {"stance": "BUY", "entry": 100, "stop": 90, "target": 130},
        bars([(108, 96, 105), (112, 104, 110)]),
    )
    assert r["outcome"] == "EXPIRED"
    assert r["r_multiple"] == pytest.approx(1.0)  # (110-100)/10


def test_sell_direction_is_mirrored():
    """SELL：停損在上、目標在下，方向相反但 R 的語意一致。"""
    r = ledger.resolve_call(
        {"stance": "SELL", "entry": 100, "stop": 110, "target": 70},
        bars([(102, 95, 96), (98, 68, 70)]),
    )
    assert r["outcome"] == "TARGET_HIT"
    assert r["r_multiple"] == pytest.approx(3.0)


def test_sell_hits_stop():
    r = ledger.resolve_call(
        {"stance": "SELL", "entry": 100, "stop": 110, "target": 70},
        bars([(112, 105, 111)]),
    )
    assert r["outcome"] == "STOP_HIT"
    assert r["r_multiple"] == pytest.approx(-1.0)


def test_no_stop_means_no_r_but_still_records_return():
    """沒給停損就算不出 R —— 誠實回報 None，另外記報酬率，不要瞎猜一個風險值。"""
    r = ledger.resolve_call(
        {"stance": "BUY", "entry": 100, "stop": None, "target": 130},
        bars([(112, 104, 110)]),
    )
    assert r["r_multiple"] is None
    assert r["pct_return"] == pytest.approx(10.0)
    assert r["outcome"] == "EXPIRED"


def test_hold_is_not_scored():
    """HOLD 是「不預測」，硬給對錯是自欺。標記為棄權，不進 R 統計。"""
    r = ledger.resolve_call(
        {"stance": "HOLD", "entry": 100, "stop": None, "target": None},
        bars([(120, 95, 118)]),
    )
    assert r["outcome"] == "ABSTAINED"
    assert r["r_multiple"] is None


def test_no_price_data_is_unresolvable_not_a_guess():
    """抓不到價格就標記無法結算，絕不可以猜一個結果污染統計。"""
    r = ledger.resolve_call(
        {"stance": "BUY", "entry": 100, "stop": 90, "target": 130},
        bars([]),
    )
    assert r["outcome"] == "NO_DATA"
    assert r["r_multiple"] is None


# --------------------------------------------------------------------------
# 帳本：只能追加、可重入
# --------------------------------------------------------------------------

def _meeting(sym="3037.TW", stances=("BUY", "SELL")):
    analysts = ["buffett", "lynch", "soros", "simons", "taleb"]
    return {
        "meeting_time": "2026-09-01T10:00:00",
        "symbols": [sym],
        "market_packet": {"technicals": {sym: {"last_price": 100.0}}},
        "consensus": {sym: {"positions": [
            {"analyst": analysts[i], "stance": s, "conviction": 7,
             "target_price": 130.0, "stop_loss": 90.0, "time_horizon": "3-6 個月"}
            for i, s in enumerate(stances)
        ]}},
    }


def test_record_extracts_one_call_per_analyst(tmp_path):
    lg = ledger.Ledger(tmp_path / "l.jsonl")
    assert lg.record(_meeting()) == 2
    calls = lg.pending()
    assert {c["analyst"] for c in calls} == {"buffett", "lynch"}
    assert calls[0]["entry"] == 100.0
    assert calls[0]["due_date"] > calls[0]["made_date"]


def test_record_is_idempotent(tmp_path):
    """同一場會議寫兩次不可以變成兩倍部位 —— 重跑腳本是常態。"""
    lg = ledger.Ledger(tmp_path / "l.jsonl")
    lg.record(_meeting())
    assert lg.record(_meeting()) == 0
    assert len(lg.pending()) == 2


def test_resolved_calls_leave_pending(tmp_path):
    lg = ledger.Ledger(tmp_path / "l.jsonl")
    lg.record(_meeting())
    lg.resolve_due(lambda sym, a, b: bars([(132, 120, 130)]),
                   today=dt.date(2027, 3, 1))  # 137天後到期日為 2027-01-16
    assert lg.pending() == []
    assert len(lg.resolved()) == 2


def test_not_yet_due_is_not_resolved(tmp_path):
    """沒到期就不准結算 —— 提早看答案會讓統計偏向短線。"""
    lg = ledger.Ledger(tmp_path / "l.jsonl")
    lg.record(_meeting())
    n = lg.resolve_due(lambda *a: bars([(132, 120, 130)]),
                       today=dt.date(2026, 9, 2))
    assert n == 0
    assert len(lg.pending()) == 2


# --------------------------------------------------------------------------
# 計分卡：從帳本推導，永不儲存
# --------------------------------------------------------------------------

def test_scorecard_computes_expectancy_and_win_rate(tmp_path):
    lg = ledger.Ledger(tmp_path / "l.jsonl")
    lg._append([
        {"call_id": "1", "analyst": "buffett", "stance": "BUY", "conviction": 8,
         "symbol": "X", "resolution": {"outcome": "TARGET_HIT", "r_multiple": 3.0}},
        {"call_id": "2", "analyst": "buffett", "stance": "BUY", "conviction": 6,
         "symbol": "X", "resolution": {"outcome": "STOP_HIT", "r_multiple": -1.0}},
        {"call_id": "3", "analyst": "buffett", "stance": "BUY", "conviction": 7,
         "symbol": "X", "resolution": {"outcome": "STOP_HIT", "r_multiple": -1.0}},
    ])
    s = lg.scorecard()["buffett"]
    assert s["n"] == 3
    assert s["win_rate"] == pytest.approx(1 / 3)
    assert s["avg_r"] == pytest.approx(1 / 3)      # (3-1-1)/3 —— 期望值為正
    assert s["payoff_ratio"] == pytest.approx(3.0)  # 平均勝3R / 平均敗1R


def test_scorecard_separates_abstentions(tmp_path):
    lg = ledger.Ledger(tmp_path / "l.jsonl")
    lg._append([
        {"call_id": "1", "analyst": "x", "stance": "BUY", "conviction": 8, "symbol": "S",
         "resolution": {"outcome": "TARGET_HIT", "r_multiple": 2.0}},
        {"call_id": "2", "analyst": "x", "stance": "HOLD", "conviction": 5, "symbol": "S",
         "resolution": {"outcome": "ABSTAINED", "r_multiple": None}},
    ])
    s = lg.scorecard()["x"]
    assert s["n"] == 1, "棄權不可以混進 R 統計"
    assert s["abstained"] == 1


def test_scorecard_reports_calibration_by_conviction(tmp_path):
    """信心度要能被驗證：說 9/10 的時候是不是真的比說 5/10 準。"""
    lg = ledger.Ledger(tmp_path / "l.jsonl")
    lg._append([
        {"call_id": str(i), "analyst": "x", "stance": "BUY", "symbol": "S",
         "conviction": conv,
         "resolution": {"outcome": "TARGET_HIT" if win else "STOP_HIT",
                        "r_multiple": 2.0 if win else -1.0}}
        for i, (conv, win) in enumerate(
            [(9, True), (9, True), (9, False), (4, False), (4, True)])
    ])
    cal = lg.scorecard()["x"]["calibration"]
    assert cal["high"]["n"] == 3 and cal["high"]["win_rate"] == pytest.approx(2 / 3)
    assert cal["low"]["n"] == 2 and cal["low"]["win_rate"] == pytest.approx(0.5)


def test_empty_ledger_does_not_crash(tmp_path):
    assert ledger.Ledger(tmp_path / "none.jsonl").scorecard() == {}


# --------------------------------------------------------------------------
# Brier 分數：p_target 估得準不準（conviction 那種感覺分數做不到的事）
# --------------------------------------------------------------------------

def test_scorecard_scores_probability_calibration(tmp_path):
    """說 90% 會到目標、結果三次中兩次到 —— 機率是可以被證偽的。"""
    lg = ledger.Ledger(tmp_path / "l.jsonl")
    lg._append([
        {"call_id": str(i), "analyst": "x", "stance": "BUY", "symbol": "S",
         "p_target": p,
         "resolution": {"outcome": "TARGET_HIT" if hit else "STOP_HIT",
                        "r_multiple": 2.0 if hit else -1.0}}
        for i, (p, hit) in enumerate([(0.9, True), (0.9, True), (0.9, False)])
    ])
    s = lg.scorecard()["x"]
    # (0.01 + 0.01 + 0.81) / 3
    assert s["brier"] == pytest.approx(0.2767, abs=1e-3)
    assert s["avg_p_target"] == pytest.approx(0.9)
    assert s["actual_hit_rate"] == pytest.approx(2 / 3)


def test_brier_ignores_calls_without_probability(tmp_path):
    """沒給機率的判斷不能混進校準統計。"""
    lg = ledger.Ledger(tmp_path / "l.jsonl")
    lg._append([
        {"call_id": "1", "analyst": "x", "stance": "BUY", "symbol": "S", "p_target": None,
         "resolution": {"outcome": "TARGET_HIT", "r_multiple": 2.0}},
    ])
    assert lg.scorecard()["x"]["brier"] is None
