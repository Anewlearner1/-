"""Chip data ("籌碼面"): 三大法人買賣超 (institutional net buy/sell) and 融資融券
(margin trading) balances, from the official TWSE / TPEX open APIs.

Best-effort by design: these are unofficial-but-public JSON endpoints, their
schemas drift over time, and TPEX (上櫃) coverage is thinner than TWSE (上市).
A symbol that fails to parse gets `{"error": ...}` instead of raising, so one
bad feed never takes down the rest of the team's data packet — the analysts'
prompt just shows "籌碼資料缺失" for that name, same as any other data gap.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Optional

import requests

TWSE_T86 = "https://www.twse.com.tw/rwd/zh/fund/T86"
TWSE_MARGIN = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
TPEX_INSTITUTIONAL = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
TPEX_MARGIN = "https://www.tpex.org.tw/web/stock/margin_trading/margin_bal/margin_bal_result.php"


def _safe_get(url: str, params: dict, timeout: int = 10) -> Optional[dict]:
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=timeout,
                             headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                print(f"  [chips] 警告: GET {url} 失敗 — {e}")
            else:
                time.sleep(1)
    return None


def _num(v) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "")
    if not s or s in ("--", "N/A"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _find_col(fields: list[str], *keywords: str) -> Optional[int]:
    for i, f in enumerate(fields):
        if all(k in f for k in keywords):
            return i
    return None


def _roc_date(d: date) -> str:
    return f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"


# --------------------------------------------------------------------------- #
# TWSE (上市)
# --------------------------------------------------------------------------- #
def _fetch_twse_institutional_day(d: date) -> dict[str, dict]:
    """Per-stock 三大法人買賣超 for one trading day. Units: shares (股)."""
    data = _safe_get(TWSE_T86, {"date": d.strftime("%Y%m%d"), "selectType": "ALL", "response": "json"})
    out: dict[str, dict] = {}
    if not data or data.get("stat") != "OK":
        return out
    fields = data.get("fields", [])
    # Column names overlap heavily as substrings (e.g. "外陸資買賣超股數(不含外資自營商)"
    # contains both "自營商" and "外資自營商"), so these are matched by *prefix*, not
    # "keyword anywhere in the string" — the latter silently cross-matches columns.
    # "外陸資...(不含外資自營商)" and "外資自營商買賣超股數" are reported separately; sum both
    # for the conventional "外資買賣超" total when both are present.
    foreign_main_i = next((i for i, f in enumerate(fields) if f.startswith("外陸資") and "買賣超" in f), None)
    foreign_dealer_i = next((i for i, f in enumerate(fields) if f.startswith("外資自營商") and "買賣超" in f), None)
    trust_i = next((i for i, f in enumerate(fields) if f.startswith("投信") and "買賣超" in f), None)
    # "自營商買賣超股數" (the combined dealer total) — excludes the "(自行買賣)"/"(避險)" breakdown
    # columns, which also start with "自營商" and contain "買賣超".
    dealer_i = next((i for i, f in enumerate(fields)
                     if f.startswith("自營商") and "買賣超" in f and "自行" not in f and "避險" not in f), None)
    total_i = _find_col(fields, "三大法人", "買賣超")
    code_i = 0
    for row in data.get("data", []):
        code = str(row[code_i]).strip()
        foreign_main = _num(row[foreign_main_i]) if foreign_main_i is not None else None
        foreign_dealer = _num(row[foreign_dealer_i]) if foreign_dealer_i is not None else None
        if foreign_main is not None and foreign_dealer is not None:
            foreign_net = foreign_main + foreign_dealer
        else:
            foreign_net = foreign_main if foreign_main is not None else foreign_dealer
        out[code] = {
            "foreign_net": foreign_net,
            "trust_net": _num(row[trust_i]) if trust_i is not None else None,
            "dealer_net": _num(row[dealer_i]) if dealer_i is not None else None,
            "total_net": _num(row[total_i]) if total_i is not None else None,
            "date": d.isoformat(),
        }
    return out


def _fetch_twse_margin_day(d: date) -> dict[str, dict]:
    """Per-stock 融資融券餘額 for one trading day. Units: shares (股).

    TWSE reports 融資 (margin) and 融券 (short) as one combined per-stock table
    with duplicate column names ("買進"/"賣出"/"前日餘額"/"今日餘額" each appear
    twice — margin columns first, short columns second), rather than as two
    separate tables. We locate the columns positionally: the first occurrence
    of "今日餘額"/"前日餘額" is 融資, the second (if present) is 融券.
    """
    data = _safe_get(TWSE_MARGIN, {"date": d.strftime("%Y%m%d"), "selectType": "ALL", "response": "json"})
    out: dict[str, dict] = {}
    if not data or data.get("stat") != "OK":
        return out

    for table in data.get("tables") or []:
        fields = table.get("fields", [])
        code_i = _find_col(fields, "代號")
        if code_i is None:
            continue  # market-level summary table has no per-stock code column
        today_idx = [i for i, f in enumerate(fields) if "今日餘額" in f]
        prev_idx = [i for i, f in enumerate(fields) if "前日餘額" in f]
        if not today_idx:
            continue
        margin_today, margin_prev = today_idx[0], (prev_idx[0] if prev_idx else None)
        short_today = today_idx[1] if len(today_idx) > 1 else None
        short_prev = prev_idx[1] if len(prev_idx) > 1 else None

        for row in table.get("data", []):
            code = str(row[code_i]).strip()
            entry = out.setdefault(code, {"date": d.isoformat()})

            m_bal = _num(row[margin_today])
            m_prev = _num(row[margin_prev]) if margin_prev is not None else None
            entry["margin_balance"] = m_bal
            entry["margin_balance_chg"] = (m_bal - m_prev) if (m_bal is not None and m_prev is not None) else None

            if short_today is not None:
                s_bal = _num(row[short_today])
                s_prev = _num(row[short_prev]) if short_prev is not None else None
                entry["short_balance"] = s_bal
                entry["short_balance_chg"] = (s_bal - s_prev) if (s_bal is not None and s_prev is not None) else None
    return out


# --------------------------------------------------------------------------- #
# TPEX (上櫃) — schema is less stable; best-effort only.
# --------------------------------------------------------------------------- #
def _fetch_tpex_institutional_day(d: date) -> dict[str, dict]:
    params = {"l": "zh-tw", "se": "EW", "t": "D", "d": _roc_date(d), "o": "json"}
    data = _safe_get(TPEX_INSTITUTIONAL, params)
    out: dict[str, dict] = {}
    if not data:
        return out
    rows = data.get("aaData") or data.get("tables", [{}])[0].get("data") if isinstance(data.get("tables"), list) else data.get("aaData")
    if not rows:
        return out
    for row in rows:
        try:
            code = str(row[0]).strip()
            out[code] = {
                "foreign_net": _num(row[10]) if len(row) > 10 else None,
                "trust_net": _num(row[13]) if len(row) > 13 else None,
                "dealer_net": _num(row[16]) if len(row) > 16 else None,
                "total_net": _num(row[-1]),
                "date": d.isoformat(),
            }
        except (IndexError, ValueError, TypeError):
            continue
    return out


def _fetch_tpex_margin_day(d: date) -> dict[str, dict]:
    params = {"l": "zh-tw", "d": _roc_date(d), "o": "json"}
    data = _safe_get(TPEX_MARGIN, params)
    out: dict[str, dict] = {}
    if not data:
        return out
    rows = data.get("aaData")
    if not rows:
        return out
    for row in rows:
        try:
            code = str(row[0]).strip()
            out[code] = {
                "margin_balance": _num(row[6]) if len(row) > 6 else None,
                "margin_balance_chg": None,
                "short_balance": _num(row[13]) if len(row) > 13 else None,
                "short_balance_chg": None,
                "date": d.isoformat(),
            }
        except (IndexError, ValueError, TypeError):
            continue
    return out


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def _recent_trading_days(n: int = 5, as_of: date | None = None):
    d = as_of or datetime.now().date()
    out = []
    while len(out) < n:
        if d.weekday() < 5:  # Mon-Fri; holidays are handled by empty responses upstream
            out.append(d)
        d -= timedelta(days=1)
    return out


def fetch_chip_snapshot(symbols: list[str], as_of: date | None = None) -> dict[str, dict]:
    """Latest available 三大法人 + 融資融券 snapshot per symbol (e.g. '2330.TW').

    Walks back up to 5 recent weekdays per market to find the most recent
    published session (handles holidays / a run right after market data isn't
    posted yet), then converts the shares totals to 張 (board lots of 1000).
    """
    codes_listed = {s.split(".")[0] for s in symbols if s.upper().endswith(".TW")}
    codes_otc = {s.split(".")[0] for s in symbols if s.upper().endswith(".TWO")}

    inst_listed: dict[str, dict] = {}
    margin_listed: dict[str, dict] = {}
    for d in _recent_trading_days(as_of=as_of):
        if codes_listed and not inst_listed:
            inst_listed = _fetch_twse_institutional_day(d)
        if codes_listed and not margin_listed:
            margin_listed = _fetch_twse_margin_day(d)
        if inst_listed or margin_listed or not codes_listed:
            break

    inst_otc: dict[str, dict] = {}
    margin_otc: dict[str, dict] = {}
    for d in _recent_trading_days(as_of=as_of):
        if codes_otc and not inst_otc:
            try:
                inst_otc = _fetch_tpex_institutional_day(d)
            except Exception as e:  # noqa: BLE001
                print(f"  [chips] TPEX 三大法人資料解析失敗 — {e}")
        if codes_otc and not margin_otc:
            try:
                margin_otc = _fetch_tpex_margin_day(d)
            except Exception as e:  # noqa: BLE001
                print(f"  [chips] TPEX 融資融券資料解析失敗 — {e}")
        if inst_otc or margin_otc or not codes_otc:
            break

    out: dict[str, dict] = {}
    for sym in symbols:
        code = sym.split(".")[0]
        is_otc = sym.upper().endswith(".TWO")
        inst = (inst_otc if is_otc else inst_listed).get(code)
        margin = (margin_otc if is_otc else margin_listed).get(code)
        if not inst and not margin:
            out[sym] = {"symbol": sym, "error": "查無籌碼資料"}
            continue

        def lots(v):
            return None if v is None else round(v / 1000, 1)

        out[sym] = {
            "symbol": sym,
            "date": (inst or margin or {}).get("date"),
            "foreign_net_lots": lots((inst or {}).get("foreign_net")),
            "trust_net_lots": lots((inst or {}).get("trust_net")),
            "dealer_net_lots": lots((inst or {}).get("dealer_net")),
            "total_net_lots": lots((inst or {}).get("total_net")),
            "margin_balance_lots": lots((margin or {}).get("margin_balance")),
            "margin_balance_chg_lots": lots((margin or {}).get("margin_balance_chg")),
            "short_balance_lots": lots((margin or {}).get("short_balance")),
            "short_balance_chg_lots": lots((margin or {}).get("short_balance_chg")),
        }
    return out
