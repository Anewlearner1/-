"""Offline tests for the chip-data (三大法人/融資融券) parsing helpers.

Network calls are monkeypatched out entirely — these test the parsing/merge
logic, not TWSE/TPEX availability.
"""
from datetime import date

import tw_team.chips as chips


def test_num_handles_commas_blanks_and_negatives():
    assert chips._num("1,234") == 1234
    assert chips._num("-567") == -567
    assert chips._num("--") is None
    assert chips._num(None) is None
    assert chips._num(42.0) == 42


def test_find_col_matches_by_keyword_combination():
    fields = ["證券代號", "證券名稱", "外資買賣超股數", "投信買賣超股數", "三大法人買賣超股數"]
    assert chips._find_col(fields, "外資", "買賣超") == 2
    assert chips._find_col(fields, "三大法人", "買賣超") == 4
    assert chips._find_col(fields, "不存在") is None


def test_fetch_chip_snapshot_merges_listed_and_otc_and_converts_to_lots(monkeypatch):
    monkeypatch.setattr(chips, "_fetch_twse_institutional_day", lambda d: {
        "2330": {"foreign_net": 2_000_000, "trust_net": 500_000, "dealer_net": -100_000,
                 "total_net": 2_400_000, "date": d.isoformat()},
    })
    monkeypatch.setattr(chips, "_fetch_twse_margin_day", lambda d: {
        "2330": {"margin_balance": 10_000_000, "margin_balance_chg": -50_000,
                 "short_balance": 1_000_000, "short_balance_chg": 20_000, "date": d.isoformat()},
    })
    monkeypatch.setattr(chips, "_fetch_tpex_institutional_day", lambda d: {})
    monkeypatch.setattr(chips, "_fetch_tpex_margin_day", lambda d: {})

    out = chips.fetch_chip_snapshot(["2330.TW", "9999.TWO"], as_of=date(2026, 9, 1))

    assert out["2330.TW"]["foreign_net_lots"] == 2000.0
    assert out["2330.TW"]["trust_net_lots"] == 500.0
    assert out["2330.TW"]["margin_balance_lots"] == 10_000.0
    assert out["2330.TW"]["margin_balance_chg_lots"] == -50.0
    assert "error" not in out["2330.TW"]

    assert out["9999.TWO"]["error"] == "查無籌碼資料"
