#!/usr/bin/env python3
"""
twse_live.py 的離線測試（不連網）。

用固定樣本（fixtures）把整條管線跑一遍：行情解析 -> 均量彙總 -> 靜態資料合併
-> Black-Scholes 反解 -> 交給 warrant_screener.py 篩選。
另外驗證數值正確性：價格↔IV 往返、買賣權平價、HV 與理論值。

    python test_twse_live.py
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import twse_live as T  # noqa: E402

W_FIELDS = ["暫停交易", "證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額",
            "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差",
            "最後揭示買價", "最後揭示買量", "最後揭示賣價", "最後揭示賣量",
            "本益比", "標的代號", "標的名稱", "標的收盤價/指數"]

FAILED: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def _wrow(code, name, vol, bid, ask, close, und, undname, undpx):
    return ["", code, name, f"{vol:,}", "10", "1,000", "1.00", "1.10", "0.90",
            f"{close}", "<p style= color:red>+</p>", "0.05", f"{bid}", "100",
            f"{ask}", "100", "0.00", und, undname, f"{undpx:,}"]


def write_fixtures(root: Path, days: list[str]) -> None:
    cache = root / ".cache_twse"
    cache.mkdir(exist_ok=True)
    for i, d in enumerate(days):
        calls = [_wrow("030079", "台積電元大89購01", 2_000_000 - i * 100_000, 1.20, 1.21, 1.20, "2330", "台積電", 2400.0),
                 _wrow("030080", "台積電凱基90購02", 50_000, 0.30, 0.35, 0.32, "2330", "台積電", 2400.0),
                 _wrow("030081", "鴻海富邦91購03", 900_000, 2.00, 2.01, 2.00, "2317", "鴻海", 250.0)]
        puts = [_wrow("070001", "台積電群益92售01", 300_000, 0.80, 0.81, 0.80, "2330", "台積電", 2400.0)]
        for t, rows, title in (("0999", calls, "認購權證(不含牛證)"),
                               ("0999P", puts, "認售權證(不含熊證)")):
            obj = {"stat": "OK", "date": d,
                   "tables": [{}, {"title": f"{d} 每日收盤行情({title})",
                                   "fields": W_FIELDS, "data": rows}]}
            (cache / f"MI_INDEX_{d}_{t}.json").write_text(
                json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    sda = {"stat": "OK",
           "fields": ["日期", "證券代號", "證券名稱", "成交股數", "成交金額", "開盤價",
                      "最高價", "最低價", "收盤價", "漲跌價差", "成交筆數"],
           "data": [["1150918", "2330", "台積電", "31,855,287", "1", "2395.00",
                     "2410.00", "2390.00", "2400.00", "5.00", "100"],
                    ["1150918", "2317", "鴻海", "12,000,000", "1", "249.0",
                     "251.0", "248.0", "250.00", "1.00", "100"]]}
    (cache / f"STOCK_DAY_ALL_{days[0]}.json").write_text(
        json.dumps(sda, ensure_ascii=False), encoding="utf-8")

    (root / "static.csv").write_text(
        "權證代號,履約價,到期日,行使比例,流通在外比例\n"
        "030079,2600,2027-03-18,0.01,35\n"
        "030080,3000,2026-11-20,0.01,80\n"
        "030081,270,2027-01-15,0.05,22\n"
        "070001,2200,2027-02-10,0.01,40\n", encoding="utf-8-sig")


def test_math() -> None:
    print("\n[1] Black-Scholes / HV 數值")
    for S, K, t, sig, r, is_call in [(100, 110, 0.5, 0.35, 0.015, True),
                                     (100, 90, 1.0, 0.22, 0.015, False),
                                     (2400, 2600, 0.49, 0.30, 0.015, True)]:
        p = T.bs_price(S, K, t, sig, r, is_call)
        iv = T.implied_vol(p, S, K, t, r, is_call)
        check(abs(iv - sig) < 1e-6, f"IV 往返 sigma={sig} -> {iv:.8f}")

    S, K, t, sig, r = 100, 105, 0.75, 0.3, 0.02
    parity = (T.bs_price(S, K, t, sig, r, True) - T.bs_price(S, K, t, sig, r, False)
              - (S - K * math.exp(-r * t)))
    check(abs(parity) < 1e-9, f"買賣權平價誤差 {parity:.2e}")

    dc = T.bs_delta(100, 100, 1, 0.3, 0.02, True)
    dp = T.bs_delta(100, 100, 1, 0.3, 0.02, False)
    check(0 < dc < 1 and -1 < dp < 0 and abs((dc - dp) - 1) < 1e-12,
          f"Delta 範圍與 call-put 關係 ({dc:.4f}, {dp:.4f})")

    check(math.isnan(T.implied_vol(200, 100, 110, 0.5, 0.015, True)),
          "價格超出無套利上界 -> NaN")
    check(math.isnan(T.implied_vol(0.0, 100, 110, 0.5, 0.015, True)),
          "價格為 0 -> NaN")

    rng = np.random.default_rng(7)
    px = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.4 / math.sqrt(252), 3000))))
    hv = T.hv_annualized(px, 250)
    check(abs(hv - 40) < 3, f"HV 由 40% 的模擬序列還原為 {hv:.2f}%")
    check(math.isnan(T.hv_annualized(pd.Series([1.0, 2.0]), 20)), "資料不足 -> NaN")


def test_parsers() -> None:
    print("\n[2] 解析器")
    check(str(T._roc_to_date("115/09/18").date()) == "2026-09-18", "民國日期 115/09/18")
    check(str(T._roc_to_date("1150918").date()) == "2026-09-18", "民國日期 1150918")
    check(T._num("1,234.5") == 1234.5, "千分位")
    check(math.isnan(T._num("--")) and math.isnan(T._num("")), "破折號／空字串 -> NaN")
    check(math.isnan(T._num("<p style= color:red>+</p>")), "HTML 標記 -> NaN")
    check(T.guess_issuer("台積電元大89購01") == "元大", "發行商推測")
    check(pd.isna(T.guess_issuer("鴻海ZZ99購01")), "認不出的發行商 -> NaN")


def test_pipeline(root: Path) -> None:
    print("\n[3] 端對端管線（離線 fixtures）")
    days = T.recent_trading_days(5)          # 相對「今天」，測試不會隨日期失效
    write_fixtures(root, days)
    os.chdir(root)
    T.REQUEST_GAP = 0.0

    day, df = T.build_table(days=5, static_path="static.csv", gap=0.0)
    check(day == days[0], f"交易日 {day}")
    check(len(df) == 4, f"權證筆數 {len(df)}（3 認購 + 1 認售）")
    check(list(df.columns) == T.SCREENER_COLS, "輸出欄位與篩選器一致")

    r = df.set_index("warrant_code").loc["030079"]
    check(r["warrant_type"] == "認購" and r["issuer"] == "元大", "類別與發行商")
    check(abs(r["avg_volume_5d"] - 1800.0) < 1e-6,
          f"5 日均量 {r['avg_volume_5d']:.1f} 張（股數平均/1000）")
    check(abs(r["underlying_volume"] - 31855.287) < 1e-3,
          f"標的量 {r['underlying_volume']:.1f} 張")
    check(abs(r["underlying_price"] - 2400.0) < 1e-9, "標的股價")

    # 自算的 IV/Delta/槓桿：用算出的 IV 回推價格，應等於市場中價
    mid = (r["bid"] + r["ask"]) / 2 / 0.01          # 行使比例 0.01
    back = T.bs_price(2400.0, 2600.0, r["days_to_expiry"] / 365,
                      r["iv"] / 100, T.RISK_FREE, True)
    check(abs(back - mid) / mid < 1e-4, f"IV 回推價格 {back:.4f} ≈ 中價 {mid:.4f}")
    lev = r["delta"] * 2400.0 / mid
    check(abs(lev - r["leverage"]) < 1e-6, f"實質槓桿 {r['leverage']:.3f} = |Δ|·S/權證單位價")
    check(0 < r["delta"] < 1, f"Delta {r['delta']:.3f}")

    put = df.set_index("warrant_code").loc["070001"]
    check(put["warrant_type"] == "認售" and 0 < put["delta"] < 1,
          f"認售 Delta 取絕對值 {put['delta']:.3f}")

    check(df["hv20"].isna().all(), "未帶 --hv 時 hv20 全空（對應加分項自動失效）")

    # 沒有靜態資料時，strike/IV 應為空而不是亂填
    _, df2 = T.build_table(days=1, static_path=None, gap=0.0)
    check(df2["strike"].isna().all() and df2["iv"].isna().all(),
          "無靜態資料 -> strike/iv 留空")
    check(df2["bid"].notna().all(), "無靜態資料時行情仍完整")


def test_screener_handoff(root: Path) -> None:
    print("\n[4] 交給 warrant_screener.py")
    _, df = T.build_table(days=5, static_path="static.csv", gap=0.0)
    df.to_csv(root / "warrants.csv", index=False, encoding="utf-8-sig")
    out = subprocess.run(
        [sys.executable, str(HERE / "warrant_screener.py"), str(root / "warrants.csv"),
         "--min-score", "1", "--min-days", "30", "--max-otm", "30",
         "--max-spread", "20", "--min-vol", "40", "--min-und-vol", "1000",
         "-o", str(root / "cand.csv")],
        capture_output=True, text=True, cwd=root)
    check(out.returncode == 0, "篩選器執行成功")
    cand = pd.read_csv(root / "cand.csv", dtype={"warrant_code": str})
    check(len(cand) >= 1, f"產生 {len(cand)} 檔候選")
    check(all(len(c) == 6 and c.startswith("0") for c in cand["warrant_code"]),
          f"權證代號保留前導 0：{list(cand['warrant_code'])}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="twse_live_test_"))
    cwd = Path.cwd()
    try:
        test_math()
        test_parsers()
        test_pipeline(tmp)
        test_screener_handoff(tmp)
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    if FAILED:
        print(f"{len(FAILED)} 項失敗：")
        for f in FAILED:
            print("  -", f)
        return 1
    print("全部通過。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
