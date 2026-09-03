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
    foreign_i = _find_col(fields, "外資", "買賣超") or _find_col(fields, "外陸資", "買賣超")
    trust_i = _find_col(fields, "投信", "買賣超")
    dealer_i = _find_col(fields, "自營商", "買賣超") if _find_col(fields, "自營商", "買賣超", "自行") is None else \
        _find_col(fields, "自營商", "買賣超", "合計")
    total_i = _find_col(fields, "三大法人", "買賣超")
    code_i = 0
    for row in data.get("data", []):
        code = str(row[code_i]).strip()
        out[code] = {
            "foreign_net": _num(row[foreign_i]) if foreign_i is not None else None,
            "trust_net": _num(row[trust_i]) if trust_i is not None else None,
            "dealer_net": _num(row[dealer_i]) if dealer_i is not None else None,
            "total_net": _num(row[total_i]) if total_i is not None else None,
            "date": d.isoformat(),
        }
    return out


def _fetch_twse_margin_day(d: date) -> dict[str, dict]:
    """Per-stock 融資融券餘額 for one trading day. Units: shares (股)."""
    data = _safe_get(TWSE_MARGIN, {"date": d.strftime("%Y%m%d"), "selectType": "ALL", "response": "json"})
    out: dict[str, dict] = {}
    if not data or data.get("stat") != "OK":
        return out

    tables = data.get("tables")
    if tables:
        for table in tables:
            fields = table.get("fields", [])
            code_i = _find_col(fields, "代號") or _find_col(fields, "股票代號") or 0
            title = table.get("title", "")
            is_margin = "融資" in title or any("融資" in f for f in fields)
            bal_i = _find_col(fields, "今日餘額")
            prev_i = _find_col(fields, "前日餘額")
            for row in table.get("data", []):
                code = str(row[code_i]).strip()
                bal = _num(row[bal_i]) if bal_i is not None else None
                prev = _num(row[prev_i]) if prev_i is not None else None
                chg = (bal - prev) if (bal is not None and prev is not None) else None
                entry = out.setdefault(code, {"date": d.isoformat()})
                if is_margin:
                    entry["margin_balance"] = bal
                    entry["margin_balance_chg"] = chg
                else:
                    entry["short_balance"] = bal
                    entry["short_balance_chg"] = chg
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
