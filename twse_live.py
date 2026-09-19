#!/usr/bin/env python3
"""
真實資料層 (twse_live.py)

把權證篩選器接上證交所的真實公開資料。

已實測可用的端點（2026-09 於 www.twse.com.tw）：
  每日收盤行情 - 認購/認售權證
    GET /exchangeReport/MI_INDEX?response=json&date=YYYYMMDD&type=0999
        type=0999  認購權證(不含牛證)   type=0999P 認售權證(不含熊證)
        type=0999B/0999C 牛證/熊證      type=0999X/0999Y 可展延牛證/熊證
    回傳欄位含：證券代號、證券名稱、成交股數、最後揭示買/賣價、
                標的代號、標的名稱、標的收盤價/指數
  個股月成交資訊（算歷史波動率用）
    GET /exchangeReport/STOCK_DAY?response=json&date=YYYYMM01&stockNo=2330
  全市場個股當日行情
    GET /exchangeReport/STOCK_DAY_ALL?response=json

證交所會擋連續請求（回 307 導向錯誤頁）。因此本模組：
  - 每個請求之間預設間隔 REQUEST_GAP 秒，且序列化（不併發）
  - 全部結果落地快取到 .cache_twse/，同一交易日的資料只抓一次
  - 偵測到 307／HTML 錯誤頁時，丟出明確的 ThrottledError 而不是靜默失敗

證交所「沒有」公開權證的履約價、到期日、行使比例、流通在外比例，
這些要從發行券商／權證資訊揭露平台取得，用 --static 帶進來（見 README）。
一旦有了履約價/到期日/行使比例，本模組會自己用 Black-Scholes 由「真實市價」
反解隱含波動率、Delta 與實質槓桿——不必再仰賴券商匯出的二手數字。

用法：
  python twse_live.py quotes                      # 抓最近一個交易日的權證行情
  python twse_live.py build -o warrants.csv --days 5 --hv \
         --static warrant_static.csv
  python twse_live.py static-template             # 產生靜態資料範本
  python warrant_screener.py warrants.csv --min-score 3
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

TWSE_BASE = "https://www.twse.com.tw/exchangeReport"
CACHE_DIR = Path(".cache_twse")
REQUEST_GAP = 4.0          # 秒；證交所擋連發，寧可慢一點
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Referer": "https://www.twse.com.tw/zh/trading/historical/mi-index.html",
}

# 權證類別代碼 -> (標準類別, 說明)
WARRANT_TYPES = {
    "0999":  ("認購", "認購權證(不含牛證)"),
    "0999P": ("認售", "認售權證(不含熊證)"),
    "0999B": ("認購", "牛證"),
    "0999C": ("認售", "熊證"),
    "0999X": ("認購", "可展延型牛證"),
    "0999Y": ("認售", "可展延型熊證"),
}
DEFAULT_TYPES = ("0999", "0999P")

# 權證簡稱裡會出現的發行券商簡稱（長的排前面，避免「元大」吃掉「元富」之外的誤判）
ISSUERS = [
    "凱基", "元大", "元富", "富邦", "國泰", "群益", "永豐", "統一", "中信", "兆豐",
    "日盛", "台新", "華南", "康和", "玉山", "第一", "大昌", "福邦", "宏遠", "亞東",
    "麥證", "摩根", "港商", "新光", "合庫", "安泰", "陽信", "王道",
]

RISK_FREE = 0.015          # 無風險利率預設值，可用 --rate 覆寫
TRADING_DAYS = 252


class TwseError(RuntimeError):
    pass


class ThrottledError(TwseError):
    """被證交所限流／WAF 擋下。"""


# ---------------------------------------------------------------------------
# HTTP：序列化 + 節流 + 快取
# ---------------------------------------------------------------------------

_last_request_at = 0.0


def _sleep_gap(gap: float) -> None:
    global _last_request_at
    wait = gap - (time.time() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.time()


def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    return CACHE_DIR / f"{safe}.json"


def _get_json(path: str, params: dict[str, Any], cache_key: str,
              gap: float = REQUEST_GAP, retries: int = 2) -> dict:
    """抓一個 JSON 端點；同一個 cache_key 只會真的連線一次。"""
    cf = _cache_path(cache_key)
    if cf.exists():
        return json.loads(cf.read_text(encoding="utf-8"))
    if requests is None:
        raise TwseError("需要 requests：pip install requests")

    url = f"{TWSE_BASE}/{path}"
    last: Exception | None = None
    for attempt in range(retries + 1):
        _sleep_gap(gap * (attempt + 1))
        try:
            r = requests.get(url, params=params, headers=HEADERS,
                             timeout=60, allow_redirects=False)
            if r.status_code in (301, 302, 307, 308):
                raise ThrottledError(
                    f"證交所回 {r.status_code} 導向錯誤頁：{url} {params}\n"
                    f"這通常代表『短時間請求太多被擋』，或該 type 代碼不存在。\n"
                    f"請等幾分鐘再試，或把 --gap 調大（目前 {gap} 秒）。"
                )
            r.raise_for_status()
            text = r.text.lstrip()
            if text.startswith("<"):
                raise ThrottledError(f"證交所回傳 HTML 錯誤頁而非 JSON：{url} {params}")
            data = r.json()
        except ThrottledError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            continue
        cf.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data
    raise TwseError(f"下載失敗：{url} {params}\n原因：{last}")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _num(v: Any) -> float:
    """'1,234.5' / '--' / '' -> float 或 NaN。"""
    s = re.sub(r"<[^>]+>", "", str(v)).replace(",", "").strip()
    if s in ("", "-", "--", "---", "nan", "None", "X"):
        return float("nan")
    try:
        return float(s)
    except ValueError:
        return float("nan")


def _roc_to_date(s: str) -> pd.Timestamp:
    """'115/09/18' 或 '1150918' -> Timestamp。"""
    s = str(s).strip()
    m = re.match(r"^(\d{2,3})[/-]?(\d{2})[/-]?(\d{2})$", s)
    if not m:
        return pd.NaT
    y, mo, d = (int(x) for x in m.groups())
    return pd.Timestamp(year=y + 1911, month=mo, day=d)


def _rows_to_df(table: dict) -> pd.DataFrame:
    """把 MI_INDEX 的 {fields, data} 轉成 DataFrame，並清掉 HTML 標記。"""
    fields = [str(f).strip() for f in table.get("fields", [])]
    data = table.get("data") or []
    df = pd.DataFrame(data, columns=fields) if fields else pd.DataFrame(data)
    return df


def recent_trading_days(n: int, end: date | None = None) -> list[str]:
    """回傳最近 n 個「可能的」交易日（YYYYMMDD，跳過週末；國定假日由 API 回無資料自然略過）。"""
    d = end or date.today()
    out: list[str] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# 1. 權證每日行情（真實資料）
# ---------------------------------------------------------------------------


def fetch_warrant_quotes(day: str, types: Iterable[str] = DEFAULT_TYPES,
                         gap: float = REQUEST_GAP) -> pd.DataFrame:
    """
    抓某一交易日的權證收盤行情。回傳欄位：
      trade_date, warrant_code, warrant_name, warrant_type, issuer,
      close, bid, ask, volume_shares, underlying, underlying_name, underlying_price
    該日休市時回傳空 DataFrame。
    """
    frames: list[pd.DataFrame] = []
    for t in types:
        if t not in WARRANT_TYPES:
            raise TwseError(f"未知的權證類別代碼 {t}，可用：{list(WARRANT_TYPES)}")
        data = _get_json("MI_INDEX", {"response": "json", "date": day, "type": t},
                         cache_key=f"MI_INDEX_{day}_{t}", gap=gap)
        if str(data.get("stat", "")).upper() not in ("OK", ""):
            continue  # 休市日：stat 會是「很抱歉，沒有符合條件的資料!」
        for table in data.get("tables", []):
            if not table.get("data") or "標的代號" not in (table.get("fields") or []):
                continue
            df = _rows_to_df(table)
            df["_type_code"] = t
            frames.append(df)
    if not frames:
        return pd.DataFrame()

    raw = pd.concat(frames, ignore_index=True)
    out = pd.DataFrame({
        "trade_date": pd.Timestamp(datetime.strptime(day, "%Y%m%d")),
        "warrant_code": raw["證券代號"].astype(str).str.strip(),
        "warrant_name": raw["證券名稱"].astype(str).str.strip(),
        "warrant_type": raw["_type_code"].map(lambda t: WARRANT_TYPES[t][0]),
        "close": raw["收盤價"].map(_num),
        "bid": raw["最後揭示買價"].map(_num),
        "ask": raw["最後揭示賣價"].map(_num),
        "volume_shares": raw["成交股數"].map(_num),
        "underlying": raw["標的代號"].astype(str).str.strip(),
        "underlying_name": raw["標的名稱"].astype(str).str.strip(),
        "underlying_price": raw["標的收盤價/指數"].map(_num),
    })
    out["issuer"] = out["warrant_name"].map(guess_issuer)
    return out.drop_duplicates(subset=["warrant_code"]).reset_index(drop=True)


def guess_issuer(name: str) -> Any:
    """由權證簡稱推測發行券商（例：『台積電元大89購01』-> 元大）。抓不到回 NaN。"""
    s = str(name)
    for iss in ISSUERS:
        if iss in s:
            return iss
    return np.nan


def latest_quotes(types: Iterable[str] = DEFAULT_TYPES, lookback: int = 7,
                  gap: float = REQUEST_GAP) -> tuple[str, pd.DataFrame]:
    """由今天往回找，抓到第一個有資料的交易日。回傳 (日期, DataFrame)。"""
    for day in recent_trading_days(lookback):
        df = fetch_warrant_quotes(day, types, gap=gap)
        if len(df):
            return day, df
    raise TwseError(f"往回 {lookback} 天都抓不到權證行情；可能是連假，或被證交所限流。")


def warrant_volume_history(days: int, end_day: str, types: Iterable[str] = DEFAULT_TYPES,
                           gap: float = REQUEST_GAP) -> pd.DataFrame:
    """
    抓最近 days 個交易日的權證成交量，算出 avg_volume_5d（張）。
    回傳欄位：warrant_code, avg_volume_5d, volume_days
    """
    end = datetime.strptime(end_day, "%Y%m%d").date()
    frames = []
    for day in recent_trading_days(days + 4, end=end):
        if len(frames) >= days:
            break
        df = fetch_warrant_quotes(day, types, gap=gap)
        if len(df):
            frames.append(df[["warrant_code", "volume_shares"]])
    if not frames:
        return pd.DataFrame(columns=["warrant_code", "avg_volume_5d", "volume_days"])
    allv = pd.concat(frames, ignore_index=True)
    g = allv.groupby("warrant_code")["volume_shares"]
    return pd.DataFrame({
        "avg_volume_5d": g.mean() / 1000.0,   # 股 -> 張
        "volume_days": g.size(),
    }).reset_index()


# ---------------------------------------------------------------------------
# 2. 標的股：當日行情 + 歷史波動率
# ---------------------------------------------------------------------------


def fetch_stock_day_all(day_tag: str | None = None, gap: float = REQUEST_GAP) -> pd.DataFrame:
    """全市場個股當日行情。回傳 underlying, close, volume_lots（張）。"""
    tag = day_tag or date.today().strftime("%Y%m%d")
    data = _get_json("STOCK_DAY_ALL", {"response": "json"},
                     cache_key=f"STOCK_DAY_ALL_{tag}", gap=gap)
    rows = data.get("data") or data.get("tables", [{}])[0].get("data", [])
    fields = data.get("fields") or data.get("tables", [{}])[0].get("fields", [])
    df = pd.DataFrame(rows, columns=[str(f).strip() for f in fields])
    return pd.DataFrame({
        "underlying": df["證券代號"].astype(str).str.strip(),
        "close": df["收盤價"].map(_num),
        "volume_lots": df["成交股數"].map(_num) / 1000.0,
    })


def fetch_stock_daily_closes(stock_no: str, months: int = 3, end: date | None = None,
                             gap: float = REQUEST_GAP) -> pd.Series:
    """抓單一個股最近 months 個月的日收盤價（index 為日期，由舊到新）。"""
    d = (end or date.today()).replace(day=1)
    series: list[pd.Series] = []
    for _ in range(months):
        tag = d.strftime("%Y%m01")
        data = _get_json("STOCK_DAY", {"response": "json", "date": tag, "stockNo": stock_no},
                         cache_key=f"STOCK_DAY_{stock_no}_{tag}", gap=gap)
        if str(data.get("stat", "")).upper() == "OK":
            df = pd.DataFrame(data.get("data") or [],
                              columns=[str(f).strip() for f in data.get("fields", [])])
            if len(df):
                idx = df["日期"].map(_roc_to_date)
                series.append(pd.Series(df["收盤價"].map(_num).to_numpy(), index=idx))
        d = (d - timedelta(days=1)).replace(day=1)
    if not series:
        return pd.Series(dtype=float)
    s = pd.concat(series).dropna()
    return s[~s.index.duplicated()].sort_index()


def hv_annualized(closes: pd.Series, window: int = 20) -> float:
    """年化歷史波動率（%）。資料不足回 NaN。"""
    closes = pd.to_numeric(closes, errors="coerce").dropna()
    if len(closes) < window + 1:
        return float("nan")
    ret = np.log(closes / closes.shift(1)).dropna().iloc[-window:]
    return float(ret.std(ddof=1) * math.sqrt(TRADING_DAYS) * 100)


def fetch_hv_table(underlyings: Iterable[str], window: int = 20, months: int = 3,
                   gap: float = REQUEST_GAP, verbose: bool = True) -> pd.DataFrame:
    """對一組標的計算 HV。每檔要 months 個請求，標的多時會很慢——先過濾再算。"""
    rows = []
    unds = sorted({str(u).strip() for u in underlyings if str(u).strip()})
    for i, und in enumerate(unds, 1):
        if verbose:
            print(f"  [hv] {i}/{len(unds)} {und}", file=sys.stderr)
        try:
            closes = fetch_stock_daily_closes(und, months=months, gap=gap)
            rows.append({"underlying": und, "hv20": hv_annualized(closes, window)})
        except TwseError as e:
            print(f"  [hv] {und} 失敗：{e}", file=sys.stderr)
            rows.append({"underlying": und, "hv20": float("nan")})
    return pd.DataFrame(rows, columns=["underlying", "hv20"])


# ---------------------------------------------------------------------------
# 3. Black-Scholes：由真實市價反解 IV、Delta、實質槓桿
# ---------------------------------------------------------------------------


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S: float, K: float, T: float, sigma: float, r: float, is_call: bool) -> float:
    if not (S > 0 and K > 0 and T > 0 and sigma > 0):
        return float("nan")
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if is_call:
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_delta(S: float, K: float, T: float, sigma: float, r: float, is_call: bool) -> float:
    if not (S > 0 and K > 0 and T > 0 and sigma > 0):
        return float("nan")
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0


def implied_vol(price: float, S: float, K: float, T: float, r: float, is_call: bool,
                lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-6) -> float:
    """二分法反解隱含波動率（小數，非百分比）。價格超出無套利區間時回 NaN。"""
    if not all(map(lambda v: isinstance(v, float) or isinstance(v, int), (price, S, K, T))):
        return float("nan")
    if not (price > 0 and S > 0 and K > 0 and T > 0):
        return float("nan")
    intrinsic = max(0.0, (S - K * math.exp(-r * T)) if is_call else (K * math.exp(-r * T) - S))
    upper = S if is_call else K * math.exp(-r * T)
    if price <= intrinsic + 1e-12 or price >= upper:
        return float("nan")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        diff = bs_price(S, K, T, mid, r, is_call) - price
        if abs(diff) < tol:
            return mid
        if diff > 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def derive_greeks(df: pd.DataFrame, rate: float = RISK_FREE,
                  today: pd.Timestamp | None = None) -> pd.DataFrame:
    """
    用真實市價 + 靜態資料（strike / days_to_expiry / exercise_ratio）算 IV、Delta、實質槓桿。
    只填補原本是空值的欄位，不覆蓋已有資料。缺靜態資料的列維持 NaN。
    """
    df = df.copy()
    for c in ("iv", "delta", "leverage"):
        if c not in df.columns:
            df[c] = np.nan
    if "exercise_ratio" not in df.columns:
        df["exercise_ratio"] = np.nan
    ratio = df["exercise_ratio"].fillna(1.0)

    mid = (df["bid"] + df["ask"]) / 2.0
    price = mid.where(mid > 0, df.get("close"))
    # 每「一單位標的」的權證價格
    unit_price = price / ratio.replace(0, np.nan)

    iv, delta, lev = [], [], []
    for i in df.index:
        S = df.at[i, "underlying_price"]
        K = df.at[i, "strike"]
        days = df.at[i, "days_to_expiry"]
        is_call = str(df.at[i, "warrant_type"]).strip() in ("認購", "call", "Call", "CALL")
        T = (days / 365.0) if pd.notna(days) and days > 0 else float("nan")
        p = unit_price.at[i]
        sig = implied_vol(p, S, K, T, rate, is_call) if pd.notna(T) and pd.notna(K) and pd.notna(S) and pd.notna(p) else float("nan")
        dl = bs_delta(S, K, T, sig, rate, is_call) if pd.notna(sig) else float("nan")
        # 實質槓桿 = |Delta| x 標的股價 / 每單位標的的權證價格
        lv = abs(dl) * S / p if pd.notna(dl) and pd.notna(p) and p > 0 else float("nan")
        iv.append(sig * 100 if pd.notna(sig) else float("nan"))
        delta.append(abs(dl) if pd.notna(dl) else float("nan"))
        lev.append(lv)

    df["iv"] = df["iv"].fillna(pd.Series(iv, index=df.index))
    df["delta"] = df["delta"].fillna(pd.Series(delta, index=df.index))
    df["leverage"] = df["leverage"].fillna(pd.Series(lev, index=df.index))
    return df


# ---------------------------------------------------------------------------
# 4. 靜態資料（履約價／到期日／行使比例／流通在外）
# ---------------------------------------------------------------------------

STATIC_ALIASES = {
    "warrant_code": ["warrant_code", "權證代號", "證券代號", "代號"],
    "strike": ["strike", "履約價", "履約價格"],
    "expiry_date": ["expiry_date", "到期日", "最後交易日", "到期日期"],
    "exercise_ratio": ["exercise_ratio", "行使比例", "行使比率"],
    "outstanding_pct": ["outstanding_pct", "流通在外比例", "流通在外比率"],
    "issuer": ["issuer", "發行券商", "發行人"],
    "days_to_event": ["days_to_event", "距事件天數"],
    "event_before_expiry": ["event_before_expiry", "事件在到期前"],
}


def load_static(path: str | Path, today: pd.Timestamp | None = None) -> pd.DataFrame:
    """
    讀使用者提供的權證靜態資料（CSV）。欄位名稱可用中文或英文，見 STATIC_ALIASES。
    至少要有 warrant_code；有 strike + expiry_date(或 days_to_expiry) 才能算 IV/Delta/槓桿。
    """
    raw = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    raw.columns = [str(c).strip() for c in raw.columns]
    out = pd.DataFrame(index=raw.index)
    for std, names in STATIC_ALIASES.items():
        col = next((n for n in names if n in raw.columns), None)
        out[std] = raw[col] if col else np.nan
    if "days_to_expiry" in raw.columns:
        out["days_to_expiry"] = raw["days_to_expiry"]
    elif "剩餘天數" in raw.columns:
        out["days_to_expiry"] = raw["剩餘天數"]
    else:
        out["days_to_expiry"] = np.nan

    out["warrant_code"] = out["warrant_code"].astype(str).str.strip()
    for c in ("strike", "exercise_ratio", "outstanding_pct", "days_to_event", "days_to_expiry"):
        out[c] = pd.to_numeric(out[c].astype(str).str.replace(",", "").str.replace("%", ""),
                               errors="coerce")

    today = today or pd.Timestamp.today().normalize()
    exp = out["expiry_date"].map(_parse_any_date)
    need = out["days_to_expiry"].isna() & exp.notna()
    out.loc[need, "days_to_expiry"] = (exp[need] - today).dt.days
    out["event_before_expiry"] = out["event_before_expiry"].map(_parse_bool)
    return out.drop(columns=["expiry_date"])


def _parse_any_date(v: Any) -> pd.Timestamp:
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none"):
        return pd.NaT
    roc = _roc_to_date(s)
    if pd.notna(roc):
        return roc
    return pd.to_datetime(s, errors="coerce")


def _parse_bool(v: Any) -> Any:
    s = str(v).strip().lower()
    if s in ("1", "true", "y", "yes", "是"):
        return True
    if s in ("0", "false", "n", "no", "否"):
        return False
    return np.nan


STATIC_TEMPLATE_COLS = ["warrant_code", "strike", "expiry_date", "exercise_ratio",
                        "outstanding_pct", "days_to_event", "event_before_expiry"]


# ---------------------------------------------------------------------------
# 5. 組出篩選器要的標準表
# ---------------------------------------------------------------------------

SCREENER_COLS = [
    "warrant_code", "underlying", "issuer", "warrant_type", "strike",
    "underlying_price", "days_to_expiry", "leverage", "bid", "ask",
    "avg_volume_5d", "underlying_volume", "iv", "delta",
    "hv20", "outstanding_pct", "days_to_event", "event_before_expiry",
]


def build_table(days: int = 5, types: Iterable[str] = DEFAULT_TYPES,
                static_path: str | None = None, with_hv: bool = False,
                hv_months: int = 3, rate: float = RISK_FREE,
                gap: float = REQUEST_GAP,
                underlyings: Iterable[str] | None = None) -> tuple[str, pd.DataFrame]:
    """把真實行情 + 靜態資料組成 warrant_screener.py 吃得下的標準表。"""
    day, quotes = latest_quotes(types, gap=gap)
    print(f"[twse] 交易日 {day}，抓到 {len(quotes)} 檔權證行情", file=sys.stderr)

    if underlyings:
        keep = {str(u).strip() for u in underlyings}
        quotes = quotes[quotes["underlying"].isin(keep)].reset_index(drop=True)
        print(f"[twse] 依標的過濾後剩 {len(quotes)} 檔", file=sys.stderr)

    df = quotes.copy()

    if days > 1:
        vol = warrant_volume_history(days, day, types, gap=gap)
        df = df.merge(vol, on="warrant_code", how="left")
        print(f"[twse] 已計算 {days} 日均量（實際取到 "
              f"{int(df['volume_days'].max()) if len(df) else 0} 個交易日）", file=sys.stderr)
    else:
        df["avg_volume_5d"] = df["volume_shares"] / 1000.0

    # 標的當日成交量（張）
    try:
        snap = fetch_stock_day_all(day, gap=gap).set_index("underlying")
        df["underlying_volume"] = df["underlying"].map(snap["volume_lots"])
        df["underlying_price"] = df["underlying_price"].fillna(df["underlying"].map(snap["close"]))
    except TwseError as e:
        print(f"[twse] 標的當日行情取得失敗（underlying_volume 將為空）：{e}", file=sys.stderr)
        df["underlying_volume"] = np.nan

    for c in ("strike", "days_to_expiry", "exercise_ratio", "outstanding_pct",
              "days_to_event", "event_before_expiry", "iv", "delta", "leverage", "hv20"):
        if c not in df.columns:
            df[c] = np.nan

    if static_path:
        st = load_static(static_path)
        df = df.merge(st, on="warrant_code", how="left", suffixes=("", "_st"))
        for c in ("strike", "days_to_expiry", "exercise_ratio", "outstanding_pct",
                  "days_to_event", "event_before_expiry", "issuer"):
            src = f"{c}_st"
            if src in df.columns:
                df[c] = df[c].where(df[c].notna(), df[src])
                df = df.drop(columns=[src])
        print(f"[twse] 已併入靜態資料：{static_path}"
              f"（{int(df['strike'].notna().sum())} 檔有履約價）", file=sys.stderr)

    df = derive_greeks(df, rate=rate)

    if with_hv:
        unds = df.loc[df["underlying"].str.fullmatch(r"\d{4,6}"), "underlying"].unique()
        print(f"[twse] 計算 {len(unds)} 檔標的的 HV20（每檔 {hv_months} 個請求，請耐心等）",
              file=sys.stderr)
        hv = fetch_hv_table(unds, months=hv_months, gap=gap)
        df["hv20"] = df["hv20"].fillna(df["underlying"].map(hv.set_index("underlying")["hv20"]))

    for c in SCREENER_COLS:
        if c not in df.columns:
            df[c] = np.nan
    return day, df[SCREENER_COLS]


def report_coverage(df: pd.DataFrame) -> None:
    print("\n【欄位覆蓋率】（空值比例太高的欄位，對應的門檻／加分項會失效）")
    na = df.isna().mean().sort_values(ascending=False)
    for c, r in na.items():
        mark = "  ← 失效" if r > 0.5 else ""
        print(f"  {c:20s} 空值 {r:5.0%}{mark}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_quotes(a: argparse.Namespace) -> int:
    day, df = latest_quotes(tuple(a.types), gap=a.gap)
    print(f"交易日 {day}｜{len(df)} 檔")
    cols = ["warrant_code", "warrant_name", "warrant_type", "issuer", "bid", "ask",
            "volume_shares", "underlying", "underlying_price"]
    pd.set_option("display.width", 200)
    pd.set_option("display.unicode.east_asian_width", True)
    print(df[cols].head(a.head).to_string(index=False))
    if a.output:
        df.to_csv(a.output, index=False, encoding="utf-8-sig")
        print(f"已輸出：{a.output}")
    return 0


def cmd_build(a: argparse.Namespace) -> int:
    day, df = build_table(days=a.days, types=tuple(a.types), static_path=a.static,
                          with_hv=a.hv, hv_months=a.hv_months, rate=a.rate, gap=a.gap,
                          underlyings=a.underlyings)
    df.to_csv(a.output, index=False, encoding="utf-8-sig")
    print(f"已輸出 {a.output}（交易日 {day}，{len(df)} 檔）")
    report_coverage(df)
    print(f"\n下一步： python warrant_screener.py {a.output} --min-score 3")
    return 0


def cmd_hv(a: argparse.Namespace) -> int:
    hv = fetch_hv_table(a.underlyings, months=a.hv_months, gap=a.gap)
    print(hv.to_string(index=False))
    if a.output:
        hv.to_csv(a.output, index=False, encoding="utf-8-sig")
    return 0


def cmd_static_template(a: argparse.Namespace) -> int:
    pd.DataFrame(columns=STATIC_TEMPLATE_COLS).to_csv(
        a.output, index=False, encoding="utf-8-sig")
    print(f"已輸出靜態資料範本：{a.output}")
    print("欄位說明：")
    print("  warrant_code        權證代號（必填，用來和行情對應）")
    print("  strike              履約價")
    print("  expiry_date         到期日（2026-12-31 或 115/12/31 都可）")
    print("  exercise_ratio      行使比例（預設 1）")
    print("  outstanding_pct     流通在外比例 %")
    print("  days_to_event       距催化事件天數（選填）")
    print("  event_before_expiry 事件日是否在到期前 1/0（選填）")
    print("\n有了 strike + expiry_date 後，IV / Delta / 實質槓桿會由真實市價自行算出。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="權證篩選器 — 證交所真實資料層")
    p.add_argument("--gap", type=float, default=REQUEST_GAP,
                   help=f"每個請求間隔秒數（預設 {REQUEST_GAP}；被限流就調大）")
    p.add_argument("--types", nargs="*", default=list(DEFAULT_TYPES),
                   help=f"權證類別代碼，預設 {' '.join(DEFAULT_TYPES)}；可用 {list(WARRANT_TYPES)}")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("quotes", help="抓最近一個交易日的權證行情")
    q.add_argument("--head", type=int, default=20)
    q.add_argument("-o", "--output")
    q.set_defaults(func=cmd_quotes)

    b = sub.add_parser("build", help="組出篩選器用的標準 CSV")
    b.add_argument("-o", "--output", default="warrants.csv")
    b.add_argument("--days", type=int, default=5, help="均量取幾個交易日（預設 5）")
    b.add_argument("--static", help="權證靜態資料 CSV（履約價／到期日／行使比例…）")
    b.add_argument("--hv", action="store_true", help="另外計算標的 HV20（慢）")
    b.add_argument("--hv-months", type=int, default=3)
    b.add_argument("--rate", type=float, default=RISK_FREE, help="無風險利率，預設 0.015")
    b.add_argument("--underlyings", nargs="*", help="只保留這些標的的權證，例如 2330 2317")
    b.set_defaults(func=cmd_build)

    h = sub.add_parser("hv", help="計算指定標的的 HV20")
    h.add_argument("underlyings", nargs="+")
    h.add_argument("--hv-months", type=int, default=3)
    h.add_argument("-o", "--output")
    h.set_defaults(func=cmd_hv)

    t = sub.add_parser("static-template", help="產生權證靜態資料範本")
    t.add_argument("-o", "--output", default="warrant_static.csv")
    t.set_defaults(func=cmd_static_template)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.func(a)
    except ThrottledError as e:
        print(f"\n被證交所限流：{e}", file=sys.stderr)
        print("建議：等 5-10 分鐘，或加大 --gap（例如 --gap 10）。"
              "已下載的資料在 .cache_twse/，重跑不會重抓。", file=sys.stderr)
        return 3
    except TwseError as e:
        print(f"\n錯誤：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
