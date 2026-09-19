#!/usr/bin/env python3
"""
twse_live.py 的離線測試（不連網）。

用固定樣本（fixtures）把整條管線跑一遍：行情解析 -> 均量彙總 -> 靜態資料合併
-> Black-Scholes 反解 -> 交給 warrant_screener.py 篩選。
另外驗證數值正確性：價格↔IV 往返、買賣權平價、HV 與理論值。

    python test_twse_live.py
"""

from __future__ import annotations

import argparse
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

    # STOCK_DAY_ALL 實測就算帶 response=json 也是回 CSV，fixture 照實模擬
    (cache / f"STOCK_DAY_ALL_{days[0]}.json").write_text(
        "日期,證券代號,證券名稱,成交股數,成交金額,開盤價,最高價,最低價,收盤價,漲跌價差,成交筆數\n"
        '"1150918","2330","台積電","31,855,287","1","2395.00","2410.00","2390.00","2400.00","5.00","100"\n'
        '"1150918","2317","鴻海","12,000,000","1","249.0","251.0","248.0","250.00","1.00","100"\n',
        encoding="utf-8")

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
    # 權證基本資料表用的是這種寫法，一開始沒認出來，到期日整欄解析失敗
    check(str(T._roc_to_date("115年10月16日").date()) == "2026-10-16", "民國日期 115年10月16日")
    check(str(T._roc_to_date("115年9月6日").date()) == "2026-09-06", "民國日期 115年9月6日（單位數）")
    check(str(T._roc_to_date("115.10.16").date()) == "2026-10-16", "民國日期 115.10.16")
    check(pd.isna(T._roc_to_date("2026-09-18")), "西元日期不當成民國")
    check(str(T._parse_any_date("2026-09-18").date()) == "2026-09-18", "西元日期走 to_datetime")
    check(pd.isna(T._roc_to_date("115年13月01日")) and pd.isna(T._roc_to_date("115年2月30日")),
          "不存在的日期 -> NaT 而不是例外")
    check(T._num("1,234.5") == 1234.5, "千分位")
    check(math.isnan(T._num("--")) and math.isnan(T._num("")), "破折號／空字串 -> NaN")
    check(math.isnan(T._num("<p style= color:red>+</p>")), "HTML 標記 -> NaN")
    check(T.guess_issuer("台積電元大89購01") == "元大", "發行商推測")
    check(pd.isna(T.guess_issuer("鴻海ZZ99購01")), "認不出的發行商 -> NaN")


def test_payload_parsing() -> None:
    print("\n[2b] 端點回傳格式（同一支端點有時 JSON 有時 CSV）")
    csv_text = ('日期,證券代號,證券名稱,收盤價,成交股數\n'
                '"1150918","2330","台積電","2400.00","31,855,287"\n')
    df = T._parse_twse_payload(csv_text, must_have="證券代號")
    check(len(df) == 1 and df.iloc[0]["證券代號"] == "2330", "CSV 版解析")

    json_text = json.dumps({"stat": "OK", "fields": ["證券代號", "收盤價"],
                            "data": [["2330", "2400.00"]]}, ensure_ascii=False)
    df = T._parse_twse_payload(json_text, must_have="證券代號")
    check(len(df) == 1 and df.iloc[0]["收盤價"] == "2400.00", "JSON 版解析")

    try:
        T._parse_twse_payload('a,b\n1,2\n', must_have="證券代號")
        check(False, "欄位不符時應報錯")
    except T.TwseError as e:
        check("找不到" in str(e), f"欄位不符時明確報錯：{str(e)[:40]}…")


def test_warrant_type_map() -> None:
    print("\n[2c] 權證類別代碼")
    check(T.WARRANT_TYPES["0999"][0] == "認購" and T.WARRANT_TYPES["0999P"][0] == "認售",
          "0999 認購 / 0999P 認售")
    check(T.WARRANT_TYPES["0999C"][0] == "認購" and T.WARRANT_TYPES["0999B"][0] == "認售",
          "0999C 牛證屬認購 / 0999B 熊證屬認售")
    check(T.WARRANT_TYPES["0999X"][0] == "認購" and T.WARRANT_TYPES["0999Y"][0] == "認售",
          "0999X 可展延牛證 / 0999Y 可展延熊證")


def test_partial_failure(root: Path) -> None:
    print("\n[5] 單一類別被擋時不整批陣亡")
    days = T.recent_trading_days(1)
    cache = root / ".cache_twse"
    missing = cache / f"MI_INDEX_{days[0]}_0999P.json"
    backup = missing.read_text(encoding="utf-8")
    missing.unlink()
    calls = T._fetch_text  # 攔截網路：讓沒有快取的請求直接視為被擋

    def fake(path, params, cache_key, gap=0.0, retries=3):
        cf = T._cache_path(cache_key)
        if cf.exists():
            return cf.read_text(encoding="utf-8")
        raise T.ThrottledError("模擬被 WAF 擋下")

    T._fetch_text = fake
    try:
        df = T.fetch_warrant_quotes(days[0], ("0999", "0999P"), gap=0.0)
        check(len(df) == 3 and set(df["warrant_type"]) == {"認購"},
              f"認售被擋仍回傳 {len(df)} 檔認購")
        try:
            T.fetch_warrant_quotes(days[0], ("0999P",), gap=0.0)
            check(False, "全部類別都被擋時應丟 ThrottledError")
        except T.ThrottledError:
            check(True, "全部類別都被擋時丟 ThrottledError")
    finally:
        T._fetch_text = calls
        missing.write_text(backup, encoding="utf-8")


PAGE_HTML = ('<!DOCTYPE html><html><head><meta name="layout" content="web"/></head><body>'
             '<div class="rwd-tables" data-api="/warrant/WARRANT_DAILY" '
             'data-date="b:2004,e:0,f:D" data-paging="10,25,50,100"></div></body></html>')


def test_discover_api(root: Path) -> None:
    print("\n[6] 端點探索（讀報表頁的 data-api，不寫死網址）")
    os.chdir(root)
    cache = root / ".cache_twse"
    cache.mkdir(exist_ok=True)
    page = T.WARRANT_REPORT_PAGES[0]
    T._cache_path(f"PAGE_{page}").write_text(PAGE_HTML, encoding="utf-8")
    api = T.discover_report_api(page, gap=0.0)
    check(api == f"{T.RWD_BASE}/warrant/WARRANT_DAILY", f"探到端點 {api}")

    T._cache_path("PAGE_/bad.html").write_text(
        '<html><body><div data-paging="10"></div></body></html>', encoding="utf-8")
    try:
        T.discover_report_api("/bad.html", gap=0.0)
        check(False, "沒有 data-api 時應報錯")
    except T.TwseError as e:
        check("data-paging" in str(e), "沒有 data-api 時列出頁面實際的 data-* 屬性")


def test_outstanding_parsing() -> None:
    print("\n[7] 流通在外比例")
    # (a) 表上只有數量 -> 自己算比例
    df = pd.DataFrame({"權證代號": ["030079", "030080"],
                       "流通在外數量": ["3,500", "0"],
                       "發行數量": ["10,000", "10,000"],
                       "履約價": ["2,600", "3000"],
                       "到期日": ["2027-03-18", "115/11/20"],
                       "行使比例": ["0.01", "0.01"]})
    out = T.parse_outstanding_table(df)
    check(abs(out.loc[0, "outstanding_pct"] - 35.0) < 1e-9,
          f"3,500/10,000 -> {out.loc[0, 'outstanding_pct']}%")
    check(out.loc[1, "outstanding_pct"] == 0.0, "流通在外 0 -> 0%（不是 NaN）")
    check(out.loc[0, "strike"] == 2600.0 and out.loc[0, "exercise_ratio"] == 0.01,
          "順手撿到履約價與行使比例")
    check("到期日" not in out.columns and "expiry_date" in out.columns, "到期日一併帶出")

    # (b) 表上已經有現成比例 -> 直接用，不再自己算
    df2 = pd.DataFrame({"證券代號": ["030079"], "流通在外比例": ["35.5%"],
                        "流通在外數量": ["1"], "發行數量": ["10"]})
    out2 = T.parse_outstanding_table(df2)
    check(out2.loc[0, "outstanding_pct"] == 35.5, "現成比例優先於自行計算")

    # (c) 欄位帶單位
    df3 = pd.DataFrame({"權證代號": ["030079"], "流通在外數量(仟單位)": ["3,500"],
                        "發行數量(仟單位)": ["10,000"]})
    check(abs(T.parse_outstanding_table(df3).loc[0, "outstanding_pct"] - 35.0) < 1e-9,
          "欄位名帶單位也認得")

    # (d) 發行數量為 0 -> NaN 而不是除以零
    df4 = pd.DataFrame({"權證代號": ["030079"], "流通在外數量": ["5"], "發行數量": ["0"]})
    check(pd.isna(T.parse_outstanding_table(df4).loc[0, "outstanding_pct"]),
          "發行數量為 0 -> NaN")

    # (e) 湊不出比例 -> 明確報錯並列出實際欄位
    try:
        T.parse_outstanding_table(pd.DataFrame({"權證代號": ["030079"], "收盤價": ["1.2"]}))
        check(False, "湊不出比例時應報錯")
    except T.TwseError as e:
        check("收盤價" in str(e), "湊不出比例時列出實際欄位")

    try:
        T.parse_outstanding_table(pd.DataFrame({"foo": ["1"]}))
        check(False, "沒有代號欄位時應報錯")
    except T.TwseError as e:
        check("權證代號" in str(e), "沒有代號欄位時說明試過哪些名稱")


def test_outstanding_payload_shapes() -> None:
    print("\n[8] 報表回傳格式（JSON / 前面有說明行的 CSV）")
    js = json.dumps({"stat": "OK", "tables": [
        {"fields": ["說明"], "data": [["x"]]},
        {"fields": ["權證代號", "流通在外數量", "發行數量"],
         "data": [["030079", "3,500", "10,000"], ["030080", "9,000", "10,000"]]}]},
        ensure_ascii=False)
    out = T.parse_outstanding_table(T._parse_twse_payload_any(js))
    check(len(out) == 2 and abs(out.loc[1, "outstanding_pct"] - 90.0) < 1e-9,
          "JSON 多張表時取資料最多的那張")

    csv_text = ('"115年09月18日 上市權證每日收盤行情資訊彙總表"\n'
                '"權證代號","流通在外數量","發行數量"\n'
                '"030079","3,500","10,000"\n')
    out = T.parse_outstanding_table(T._parse_twse_payload_any(csv_text))
    check(len(out) == 1 and abs(out.loc[0, "outstanding_pct"] - 35.0) < 1e-9,
          "CSV 前面有標題行也能正確定位表頭")


def test_outstanding_in_build(root: Path) -> None:
    print("\n[9] 流通在外併入 build（只補空值、失敗不中斷）")
    os.chdir(root)
    (root / "outs.csv").write_text(
        "權證代號,流通在外數量,發行數量\n030079,3500,10000\n030081,9000,10000\n",
        encoding="utf-8-sig")
    _, df = T.build_table(twse_static=False, days=1, static_path="static.csv", gap=0.0,
                          outstanding_csv=str(root / "outs.csv"))
    d = df.set_index("warrant_code")
    check(d.loc["030079", "outstanding_pct"] == 35.0,
          "static.csv 已有 35 且檔案也是 35 -> 35")
    check(d.loc["030080", "outstanding_pct"] == 80.0,
          "檔案沒這檔 -> 保留 static.csv 的 80（不被蓋成空）")
    check(d.loc["030081", "outstanding_pct"] == 22.0,
          "static.csv 已有 22，檔案的 90 不覆蓋既有值")

    _, df2 = T.build_table(twse_static=False, days=1, static_path=None, gap=0.0,
                           outstanding_csv=str(root / "outs.csv"))
    d2 = df2.set_index("warrant_code")
    check(d2.loc["030081", "outstanding_pct"] == 90.0,
          "沒有 static 時 -> 用檔案算出的 90")
    check(pd.isna(d2.loc["070001", "outstanding_pct"]), "檔案裡沒有的權證留空")

    # 使用者打錯檔名要立刻知道，不能默默當成「沒有這份資料」
    try:
        T.build_table(twse_static=False, days=1, gap=0.0, outstanding_csv=str(root / "missing.csv"))
        check(False, "指定的檔案不存在時應報錯")
    except T.TwseError as e:
        check("檔案不存在" in str(e), "指定的檔案不存在時明確報錯")

    # 但「向證交所抓」失敗只留空，不中斷整批
    real = T.fetch_outstanding
    T.fetch_outstanding = lambda *a, **k: (_ for _ in ()).throw(T.ThrottledError("模擬被擋"))
    try:
        _, df4 = T.build_table(twse_static=False, days=1, gap=0.0, with_outstanding=True)
        check(len(df4) == 4 and df4["outstanding_pct"].isna().all(),
              "線上抓流通在外失敗時只留空，不中斷整批")
    finally:
        T.fetch_outstanding = real


# 使用者在自己的網路上跑 /rwd/zh/stock/warrantStock 實際回傳的欄位（一字不改）
REAL_STATIC_FIELDS = ['權證代號', '權證簡稱', '收盤價', '漲跌', '標的代號', '標的名稱',
                      '收盤價/指數', '漲跌', '權證類型', '履約方式', '上市日期',
                      '履約開始日', '最後交易日', '履約截止日', '行使比例',
                      '履約價格(元)/點數', '上限價格(元)/點數', '下限價格(元)/點數']
REAL_STATIC_ROWS = [
    ["030079", "南亞統一59購01", "20.00", "0.00", "1303", "南亞", "238.00", "1.50",
     "認購", "歐式", "114年09月18日", "115年09月18日", "116年03月17日", "116年03月18日", "0.010",
     "2,600.00", "--", "--"],
    ["070001", "台積電群益5A售12", "0.80", "-0.02", "2330", "台積電", "2,400.00", "5.00",
     "認售", "歐式", "115年01月05日", "115年02月05日", "116年02月09日", "116年02月10日", "0.0100",
     "2,200.00", "--", "--"],
]


def test_duplicate_columns() -> None:
    print("\n[10] 重複欄位名（權證收盤價與標的收盤價都叫「收盤價」）")
    cols = T._dedupe_columns(REAL_STATIC_FIELDS)
    check(len(set(cols)) == len(cols), "重複欄位名被去重")
    check(cols[2] == "收盤價" and cols[6] == "收盤價/指數" and cols[3] == "漲跌"
          and cols[7] == "漲跌.1", f"第二個「漲跌」變成 漲跌.1（{cols[3]}, {cols[7]}）")

    js = json.dumps({"stat": "OK", "tables": [{"fields": REAL_STATIC_FIELDS,
                                               "data": REAL_STATIC_ROWS}]},
                    ensure_ascii=False)
    df = T._parse_twse_payload_any(js)
    check(isinstance(df["收盤價"], pd.Series), "df[\"收盤價\"] 是 Series 而不是 DataFrame")


def test_warrant_static() -> None:
    print("\n[11] 權證基本資料（用真實欄位名）")
    js = json.dumps({"stat": "OK", "tables": [{"fields": REAL_STATIC_FIELDS,
                                               "data": REAL_STATIC_ROWS}]},
                    ensure_ascii=False)
    out = T.parse_warrant_static(T._parse_twse_payload_any(js))
    r = out.set_index("warrant_code")
    check(list(out["warrant_code"]) == ["030079", "070001"], "權證代號")
    check(r.loc["030079", "strike"] == 2600.0, f"履約價 2,600.00 -> {r.loc['030079','strike']}")
    check(r.loc["030079", "exercise_ratio"] == 0.01, "行使比例")
    check(r.loc["030079", "underlying"] == "1303", "標的代號")
    check(r.loc["030079", "warrant_type"] == "認購" and r.loc["070001", "warrant_type"] == "認售",
          "權證類型正規化")
    check(r.loc["030079", "expiry_date"] == "116年03月18日",
          "到期日取履約截止日（不是最後交易日）")
    check(pd.isna(r.loc["030079", "cap_price"]), "上限價格 '--' -> NaN")

    # 民國到期日換算成剩餘天數
    df = pd.DataFrame({"warrant_code": ["030079"], "days_to_expiry": [np.nan],
                       "expiry_date": ["116年03月18日"]})
    got = T._fill_days_to_expiry(df)
    want = (pd.Timestamp("2027-03-18") - pd.Timestamp.today().normalize()).days
    check(got.loc[0, "days_to_expiry"] == want, f"116年03月18日 -> 剩餘 {want} 天")
    check("expiry_date" not in got.columns, "換算後收掉 expiry_date")

    try:
        T.parse_warrant_static(pd.DataFrame({"權證代號": ["030079"], "收盤價": ["1"]}))
        check(False, "缺履約價時應報錯")
    except T.TwseError as e:
        check("履約價" in str(e) and "收盤價" in str(e), "缺履約價時列出實際欄位")


def test_grouped_csv_header() -> None:
    print("\n[12] CSV 的分組表頭（第一列是「權證收盤資訊,,,,標的收盤資訊,,,」）")
    csv_text = (',,,,,,,,,,,,,,,,,,\n'
                '"權證收盤資訊","","","","標的收盤資訊","","","","權證基本資訊","","","","","","","","",""\n'
                + ",".join(f'"{c}"' for c in REAL_STATIC_FIELDS) + "\n"
                + ",".join(f'"{c}"' for c in REAL_STATIC_ROWS[0]) + "\n")
    df = T._parse_csv_table(csv_text)
    check(list(df.columns)[:2] == ["權證代號", "權證簡稱"],
          f"跳過分組列，抓到真正的表頭（{list(df.columns)[:2]}）")
    out = T.parse_warrant_static(df)
    check(out.loc[0, "strike"] == 2600.0, "分組表頭的 CSV 也能解析出履約價")


def test_static_in_build(root: Path) -> None:
    print("\n[13] 證交所基本資料併入 build -> 自動算出 IV/Delta/槓桿")
    os.chdir(root)
    real = T.fetch_warrant_static
    _e = pd.Timestamp.today().normalize() + pd.Timedelta(days=180)
    exp = f"{_e.year - 1911}年{_e.month:02d}月{_e.day:02d}日"   # 證交所的真實寫法
    T.fetch_warrant_static = lambda *a, **k: pd.DataFrame({
        "warrant_code": ["030079", "030081"],
        "strike": [2600.0, 270.0], "exercise_ratio": [0.01, 0.05],
        "warrant_type": ["認購", "認購"], "expiry_date": [exp, exp],
        "underlying": ["2330", "2317"]})
    try:
        _, df = T.build_table(days=1, gap=0.0, twse_static=True)   # 沒有 --static
        d = df.set_index("warrant_code")
        check(d.loc["030079", "strike"] == 2600.0, "履約價來自證交所，不需要 --static")
        check(d.loc["030079", "days_to_expiry"] == 180, "到期日換算成剩餘天數")
        check(pd.notna(d.loc["030079", "iv"]) and pd.notna(d.loc["030079", "delta"])
              and pd.notna(d.loc["030079", "leverage"]),
              f"IV/Delta/槓桿自動算出（IV={d.loc['030079','iv']:.1f}%）")
        check(pd.isna(d.loc["030080", "iv"]), "基本資料裡沒有的權證仍留空")

        T.fetch_warrant_static = lambda *a, **k: (_ for _ in ()).throw(
            T.ThrottledError("模擬被擋"))
        _, df2 = T.build_table(days=1, gap=0.0, twse_static=True)
        check(len(df2) == 4 and df2["strike"].isna().all(),
              "基本資料抓不到時只留空，不中斷整批")
    finally:
        T.fetch_warrant_static = real


def test_hv_prefilter(root: Path) -> None:
    print("\n[14] --hv 只算通過硬性門檻的標的")
    os.chdir(root)
    calls: list[list[str]] = []

    def fake_hv(unds, months=3, gap=0.0, verbose=True):
        unds = sorted(unds)
        calls.append(unds)
        return pd.DataFrame({"underlying": unds, "hv20": [30.0] * len(unds)})

    real_hv, real_static = T.fetch_hv_table, T.fetch_warrant_static
    _e = pd.Timestamp.today().normalize() + pd.Timedelta(days=180)
    exp = f"{_e.year - 1911}年{_e.month:02d}月{_e.day:02d}日"
    # 030079 撐得過硬門檻（價外 2%、槓桿約 4.5、價差 0.8%、5 日均量 1800 張）；
    # 030080 的 5 日均量只有 50 張，會被剔除
    T.fetch_warrant_static = lambda *a, **k: pd.DataFrame({
        "warrant_code": ["030079", "030080"], "strike": [2450.0, 3000.0],
        "exercise_ratio": [0.004, 0.01], "warrant_type": ["認購", "認購"],
        "expiry_date": [exp, exp], "underlying": ["2330", "2330"]})
    T.fetch_hv_table = fake_hv
    try:
        _, df = T.build_table(days=5, gap=0.0, with_hv=True)
        d = df.set_index("warrant_code")
        check(3.0 <= d.loc["030079", "leverage"] <= 6.0,
              f"030079 槓桿 {d.loc['030079', 'leverage']:.2f} 在門檻內")
        check(len(calls) == 1, "只呼叫一次 HV 計算")
        check(calls[0] == ["2330"], f"只算通過硬門檻的標的：{calls[0]}")

        calls.clear()
        _, df2 = T.build_table(days=5, gap=0.0, with_hv=True, hv_all=True)
        check(calls[0] == ["2317", "2330"], f"--hv-all 算全部個股標的：{calls[0]}")
        check("IX0001" not in calls[0], "指數標的不送進 STOCK_DAY")
    finally:
        T.fetch_hv_table, T.fetch_warrant_static = real_hv, real_static


def test_score_max(root: Path) -> None:
    print("\n[15] 實際滿分（缺資料的加分項不算進滿分）")
    os.chdir(root)
    import warrant_screener as W

    _, df = T.build_table(twse_static=False, days=1, static_path="static.csv", gap=0.0)
    avail = W.scorable_items(df)
    check(avail["同標的IV最低"] is True and avail["Delta 0.4-0.6"] is True,
          "有履約價 -> IV/Delta 算得出來，這兩項拿得到")
    check(avail["IV低於HV"] is False, "沒跑 --hv -> 這項拿不到")
    check(avail["流通在外低"] is True, "static.csv 有流通在外 -> 這項拿得到")
    check(avail["事件距離足夠"] is False and avail["涵蓋事件日"] is False,
          "沒有事件資料 -> 這兩項拿不到")
    check(sum(avail.values()) == 3, f"實際滿分 3 而不是 6（{sum(avail.values())}）")

    df2 = df.copy()
    df2["outstanding_pct"] = np.nan
    check(sum(W.scorable_items(df2).values()) == 2,
          f"再抽掉流通在外 -> 實際滿分 2（{sum(W.scorable_items(df2).values())}）")
    df2["iv"] = 40.0
    df2["delta"] = 0.5

    scored = W.apply_scoring(W.apply_hard_filters(W.add_derived(df2), W.HardFilter()),
                             W.SOFT)
    check((scored["score_max"] == 2).all(), "score_max 寫進每一列，輸出的 CSV 也看得到")
    check((scored["score"] <= scored["score_max"]).all(), "得分不會超過實際滿分")


def test_greeks_diagnostics(capsys_unused=None) -> None:
    print("\n[16] 算不出 IV 時的原因統計")
    base = {"warrant_type": "認購", "underlying_price": 100.0, "exercise_ratio": 1.0}
    df = pd.DataFrame([
        {**base, "strike": 100.0, "days_to_expiry": 180, "bid": 8.0, "ask": 8.1},   # 正常
        {**base, "strike": 100.0, "days_to_expiry": 180, "bid": np.nan, "ask": np.nan,
         "close": np.nan},                                                          # 無報價
        {**base, "strike": np.nan, "days_to_expiry": 180, "bid": 8.0, "ask": 8.1},  # 缺履約價
        {**base, "strike": 100.0, "days_to_expiry": 0, "bid": 8.0, "ask": 8.1},     # 已到期
        {**base, "strike": 100.0, "days_to_expiry": 180, "bid": 120.0, "ask": 121.0},  # 超出上界
    ])
    out = T.derive_greeks(df, verbose=False)
    check(pd.notna(out.loc[0, "iv"]) and pd.notna(out.loc[0, "leverage"]),
          f"正常那筆算得出來（IV={out.loc[0, 'iv']:.1f}%）")
    check(out.loc[1:, "iv"].isna().all(), "其餘四種情況都留 NaN，不會硬掰數字")
    check(out.loc[1:, "leverage"].isna().all(), "算不出 IV 時槓桿也不給值")


def test_coverage_partial(root: Path) -> None:
    print("\n[17] 覆蓋率報告：hv20 只算一部分不該標成失效")
    os.chdir(root)
    real = T.fetch_hv_table
    T.fetch_hv_table = lambda unds, **k: pd.DataFrame(
        {"underlying": list(unds), "hv20": [30.0] * len(list(unds))})
    try:
        _, df = T.build_table(twse_static=False, days=1, static_path="static.csv",
                              gap=0.0, with_hv=True)
        check("hv20" in df.attrs.get("partial_cols", []),
              "有套硬門檻時，hv20 被標記為「只算了一部分」")
        _, df2 = T.build_table(twse_static=False, days=1, static_path="static.csv",
                               gap=0.0, with_hv=True, hv_all=True)
        check(df2.attrs.get("partial_cols") == [],
              "--hv-all 時不標記（本來就算全部）")
    finally:
        T.fetch_hv_table = real


def test_read_any_table(root: Path) -> None:
    print("\n[18] 匯出檔：CSV / Big5 / 缺檔 / 驗檔指令")
    os.chdir(root)
    (root / "utf8.csv").write_text("權證代號,流通在外比例\n030079,35.5\n", encoding="utf-8-sig")
    (root / "big5.csv").write_bytes(
        "權證代號,流通在外數量,發行數量\n030079,3500,10000\n".encode("big5"))

    check(len(T.read_any_table(root / "utf8.csv")) == 1, "UTF-8 CSV")
    df = T.read_any_table(root / "big5.csv")
    check(list(df.columns) == ["權證代號", "流通在外數量", "發行數量"],
          f"Big5 CSV（券商匯出常見）：{list(df.columns)}")
    check(T.load_outstanding_csv(root / "big5.csv").loc[0, "outstanding_pct"] == 35.0,
          "Big5 檔也算得出比例")

    try:
        T.read_any_table(root / "nope.csv")
        check(False, "檔案不存在時應報錯")
    except T.TwseError as e:
        check("檔案不存在" in str(e), "檔案不存在時明確報錯")

    ns = argparse.Namespace(file=str(root / "utf8.csv"))
    check(T.cmd_check_outstanding(ns) == 0, "check-outstanding 對可用的檔回傳 0")
    (root / "bad.csv").write_text("代碼,價格\n030079,1.2\n", encoding="utf-8-sig")
    ns2 = argparse.Namespace(file=str(root / "bad.csv"))
    check(T.cmd_check_outstanding(ns2) == 1, "check-outstanding 對不能用的檔回傳 1")
    ns3 = argparse.Namespace(file=str(root / "nope.csv"))
    check(T.cmd_check_outstanding(ns3) == 2, "check-outstanding 對讀不到的檔回傳 2")


def test_pipeline(root: Path) -> None:
    print("\n[3] 端對端管線（離線 fixtures）")
    days = T.recent_trading_days(5)          # 相對「今天」，測試不會隨日期失效
    write_fixtures(root, days)
    os.chdir(root)
    T.REQUEST_GAP = 0.0

    day, df = T.build_table(twse_static=False, days=5, static_path="static.csv", gap=0.0)
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
    _, df2 = T.build_table(twse_static=False, days=1, static_path=None, gap=0.0)
    check(df2["strike"].isna().all() and df2["iv"].isna().all(),
          "無靜態資料 -> strike/iv 留空")
    check(df2["bid"].notna().all(), "無靜態資料時行情仍完整")


def test_screener_handoff(root: Path) -> None:
    print("\n[4] 交給 warrant_screener.py")
    _, df = T.build_table(twse_static=False, days=5, static_path="static.csv", gap=0.0)
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
        test_payload_parsing()
        test_warrant_type_map()
        test_pipeline(tmp)
        test_screener_handoff(tmp)
        test_partial_failure(tmp)
        test_discover_api(tmp)
        test_outstanding_parsing()
        test_outstanding_payload_shapes()
        test_outstanding_in_build(tmp)
        test_duplicate_columns()
        test_warrant_static()
        test_grouped_csv_header()
        test_static_in_build(tmp)
        test_hv_prefilter(tmp)
        test_score_max(tmp)
        test_greeks_diagnostics()
        test_coverage_partial(tmp)
        test_read_any_table(tmp)
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
