"""Taiwan market data layer (yfinance) + technical feature computation.

Produces a `MarketPacket`: everything the analysts need, plus a flat
`prices` dict used to mark the paper portfolio. Adds Taiwan-specific fields
on top of the generic technical feature set: daily ±10% price-limit flags
(漲停/跌停) and OTC (上櫃) tagging, both of which the risk engine and stop-loss
sweep need to avoid pretending a stopped-out order actually filled.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

import numpy as np
import pandas as pd

from analyzer import analyze_symbol, compute_rsi, compute_sma
from .config import MACRO_SYMBOLS, OTC_SYMBOLS, RISK, SECTOR_ETFS, SECTOR_MAP, WATCHLIST, RUNTIME


@dataclass
class MarketPacket:
    as_of: str
    macro: dict[str, dict] = field(default_factory=dict)
    sectors: dict[str, dict] = field(default_factory=dict)
    stocks: dict[str, dict] = field(default_factory=dict)
    fundamentals: dict[str, dict] = field(default_factory=dict)
    chips: dict[str, dict] = field(default_factory=dict)
    prices: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "macro": self.macro,
            "sectors": self.sectors,
            "stocks": self.stocks,
            "fundamentals": self.fundamentals,
            "chips": self.chips,
            "prices": self.prices,
            "errors": self.errors,
        }


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def fetch_history(symbols: Iterable[str], period: str = "1y",
                  interval: str = "1d") -> dict[str, pd.DataFrame]:
    """Download OHLCV for many symbols in one batch, falling back per symbol."""
    import yfinance as yf

    symbols = list(dict.fromkeys(symbols))
    out: dict[str, pd.DataFrame] = {}
    try:
        raw = yf.download(symbols, period=period, interval=interval,
                          group_by="ticker", auto_adjust=True, progress=False,
                          threads=True)
        if isinstance(raw.columns, pd.MultiIndex):
            for sym in symbols:
                if sym in raw.columns.get_level_values(0):
                    df = raw[sym].dropna(how="all")
                    if not df.empty:
                        out[sym] = df
        elif len(symbols) == 1 and not raw.empty:
            out[symbols[0]] = raw.dropna(how="all")
    except Exception as e:  # noqa: BLE001
        print(f"  [data] 批次下載失敗，改為逐檔下載 — {e}")

    missing = [s for s in symbols if s not in out]
    for sym in missing:
        for attempt in range(2):
            try:
                df = yf.Ticker(sym).history(period=period, interval=interval, auto_adjust=True)
                if not df.empty:
                    out[sym] = df
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 1:
                    print(f"  [data] {sym} 下載失敗 — {e}")
                time.sleep(1)
    return out


_FUNDAMENTAL_KEYS = {
    "name": ("shortName", "longName"),
    "market_cap": ("marketCap",),
    "trailing_pe": ("trailingPE",),
    "forward_pe": ("forwardPE",),
    "peg": ("pegRatio", "trailingPegRatio"),
    "price_to_sales": ("priceToSalesTrailing12Months",),
    "revenue_growth": ("revenueGrowth",),
    "earnings_growth": ("earningsGrowth", "earningsQuarterlyGrowth"),
    "gross_margin": ("grossMargins",),
    "operating_margin": ("operatingMargins",),
    "profit_margin": ("profitMargins",),
    "dividend_yield": ("dividendYield",),
    "free_cash_flow": ("freeCashflow",),
    "debt_to_equity": ("debtToEquity",),
    "beta": ("beta",),
    "analyst_target": ("targetMeanPrice",),
    "recommendation": ("recommendationKey",),
    "next_earnings": ("earningsTimestamp",),
}


def fetch_fundamentals(symbols: Iterable[str]) -> dict[str, dict]:
    """Pull a compact fundamental snapshot per symbol (slow: one request each)."""
    import yfinance as yf

    out: dict[str, dict] = {}
    for sym in symbols:
        try:
            info = yf.Ticker(sym).info or {}
        except Exception as e:  # noqa: BLE001
            out[sym] = {"symbol": sym, "error": str(e)}
            continue
        row: dict = {"symbol": sym, "sector": SECTOR_MAP.get(sym, info.get("sector", "未知"))}
        for key, candidates in _FUNDAMENTAL_KEYS.items():
            val = None
            for c in candidates:
                if info.get(c) is not None:
                    val = info[c]
                    break
            row[key] = val
        if isinstance(row.get("next_earnings"), (int, float)):
            row["next_earnings"] = datetime.fromtimestamp(row["next_earnings"]).date().isoformat()
        out[sym] = row
    return out


# --------------------------------------------------------------------------- #
# Feature computation
# --------------------------------------------------------------------------- #
def _pct(a: float, b: float) -> float | None:
    if a is None or b is None or b == 0 or np.isnan(a) or np.isnan(b):
        return None
    return round((a / b - 1.0) * 100, 2)


def _last(series: pd.Series) -> float | None:
    s = series.dropna()
    return float(s.iloc[-1]) if len(s) else None


def enrich_daily(df: pd.DataFrame, symbol: str, benchmark: pd.DataFrame | None = None) -> dict:
    """Swing-trading feature set on daily bars, layered on `analyzer.analyze_symbol`.

    Adds Taiwan-specific flags: `limit_up`/`limit_down` (±price_limit_pct from the
    prior close — yfinance's adjusted close can drift slightly around ex-dividend
    days, so this is a heuristic flag for the risk engine, not an exact match to
    the exchange's own limit price) and `is_otc`.
    """
    base = analyze_symbol(df, symbol)
    if "error" in base:
        return base

    close = df["Close"].dropna()
    high = df["High"].dropna() if "High" in df else close
    low = df["Low"].dropna() if "Low" in df else close
    n = len(close)
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if n > 1 else last

    def ret(days: int) -> float | None:
        return _pct(last, float(close.iloc[-1 - days])) if n > days else None

    sma50 = _last(compute_sma(close, 50)) if n >= 50 else None
    sma200 = _last(compute_sma(close, 200)) if n >= 200 else None

    # ATR(14) as % of price — position sizing input
    atr_pct = None
    if n >= 15 and "High" in df and "Low" in df:
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        atr_pct = round(float(atr) / last * 100, 2) if not np.isnan(atr) else None

    hi_52w = float(close.tail(252).max())
    lo_52w = float(close.tail(252).min())

    rs_1m = rs_3m = None
    if benchmark is not None and not benchmark.empty:
        b = benchmark["Close"].dropna()
        if len(b) > 63 and n > 63:
            rs_1m = round((ret(21) or 0) - (_pct(float(b.iloc[-1]), float(b.iloc[-22])) or 0), 2)
            rs_3m = round((ret(63) or 0) - (_pct(float(b.iloc[-1]), float(b.iloc[-64])) or 0), 2)

    daily_vol = None
    if n >= 21:
        daily_vol = round(float(close.pct_change().tail(20).std() * np.sqrt(252) * 100), 1)

    change_pct = _pct(last, prev) or 0.0
    limit_up = change_pct >= RISK.price_limit_pct * 100 - 0.15
    limit_down = change_pct <= -RISK.price_limit_pct * 100 + 0.15

    base.update({
        "ret_1w_pct": ret(5),
        "ret_1m_pct": ret(21),
        "ret_3m_pct": ret(63),
        "ret_6m_pct": ret(126),
        "sma50": round(sma50, 2) if sma50 else None,
        "sma200": round(sma200, 2) if sma200 else None,
        "above_sma50": (last > sma50) if sma50 else None,
        "above_sma200": (last > sma200) if sma200 else None,
        "atr_pct": atr_pct,
        "ann_vol_pct": daily_vol,
        "high_52w": round(hi_52w, 2),
        "low_52w": round(lo_52w, 2),
        "dist_52w_high_pct": _pct(last, hi_52w),
        "rs_vs_taiex_1m": rs_1m,
        "rs_vs_taiex_3m": rs_3m,
        "sector": SECTOR_MAP.get(symbol) or SECTOR_ETFS.get(symbol) or "",
        "is_otc": symbol.upper() in OTC_SYMBOLS or symbol.upper().endswith(".TWO"),
        "limit_up": limit_up,
        "limit_down": limit_down,
    })
    return base


def build_packet(history: dict[str, pd.DataFrame], fundamentals: dict[str, dict] | None = None,
                 chips: dict[str, dict] | None = None,
                 macro_symbols: list[str] | None = None,
                 sector_symbols: Iterable[str] | None = None,
                 watchlist: list[str] | None = None) -> MarketPacket:
    macro_symbols = macro_symbols or MACRO_SYMBOLS
    sector_symbols = list(sector_symbols or SECTOR_ETFS.keys())
    watchlist = watchlist or WATCHLIST
    taiex = history.get("^TWII")

    packet = MarketPacket(as_of=datetime.now().isoformat(timespec="seconds"),
                          fundamentals=fundamentals or {}, chips=chips or {})
    for sym in macro_symbols:
        df = history.get(sym)
        packet.macro[sym] = enrich_daily(df, sym) if df is not None else {"symbol": sym, "error": "無資料"}
    for sym in sector_symbols:
        df = history.get(sym)
        packet.sectors[sym] = enrich_daily(df, sym, taiex) if df is not None else {"symbol": sym, "error": "無資料"}
    for sym in watchlist:
        df = history.get(sym)
        packet.stocks[sym] = enrich_daily(df, sym, taiex) if df is not None else {"symbol": sym, "error": "無資料"}

    for sym, df in history.items():
        if df is not None and not df.empty and "Close" in df:
            packet.prices[sym] = float(df["Close"].dropna().iloc[-1])

    for group in (packet.macro, packet.sectors, packet.stocks):
        for sym, row in group.items():
            if "error" in row:
                packet.errors.append(f"{sym}: {row['error']}")
    return packet


def collect_market_data(watchlist: list[str] | None = None,
                        extra_symbols: Iterable[str] = (),
                        with_fundamentals: bool = True,
                        with_chips: bool = True) -> MarketPacket:
    """Fetch everything and build the packet. `extra_symbols` covers held positions."""
    from .chips import fetch_chip_snapshot

    watchlist = watchlist or WATCHLIST
    symbols = list(dict.fromkeys([*MACRO_SYMBOLS, *SECTOR_ETFS.keys(), *watchlist, *extra_symbols]))
    print(f"  [data] 下載 {len(symbols)} 檔歷史資料 ({RUNTIME.history_period}, {RUNTIME.history_interval})...")
    history = fetch_history(symbols, period=RUNTIME.history_period, interval=RUNTIME.history_interval)
    stock_symbols = list(dict.fromkeys([*watchlist, *extra_symbols]))
    fundamentals: dict[str, dict] = {}
    if with_fundamentals:
        print(f"  [data] 抓取 {len(stock_symbols)} 檔基本面...")
        fundamentals = fetch_fundamentals(stock_symbols)
    chips: dict[str, dict] = {}
    if with_chips:
        print(f"  [data] 抓取 {len(stock_symbols)} 檔籌碼面（三大法人/融資融券）...")
        chips = fetch_chip_snapshot(stock_symbols)
    return build_packet(history, fundamentals, chips, watchlist=stock_symbols)


# --------------------------------------------------------------------------- #
# Prompt formatting
# --------------------------------------------------------------------------- #
def _fmt(v, suffix: str = "") -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float):
        if abs(v) >= 1e8:
            return f"{v/1e8:.1f}億{suffix}"
        return f"{v:.2f}{suffix}"
    return f"{v}{suffix}"


def _row_line(r: dict) -> str:
    if "error" in r:
        return f"- {r['symbol']}: 資料缺失 ({r['error']})"
    flags = []
    if r.get("limit_up"):
        flags.append("漲停")
    if r.get("limit_down"):
        flags.append("跌停")
    if r.get("is_otc"):
        flags.append("上櫃")
    flag_str = f"｜{'/'.join(flags)}" if flags else ""
    return (
        f"- {r['symbol']}: 價 {_fmt(r.get('last_price'))}｜日 {_fmt(r.get('change_pct'), '%')}"
        f"｜1週 {_fmt(r.get('ret_1w_pct'), '%')}｜1月 {_fmt(r.get('ret_1m_pct'), '%')}"
        f"｜3月 {_fmt(r.get('ret_3m_pct'), '%')}｜RSI {_fmt(r.get('rsi'))}"
        f"｜>MA50 {_fmt(r.get('above_sma50'))}｜>MA200 {_fmt(r.get('above_sma200'))}"
        f"｜距52週高 {_fmt(r.get('dist_52w_high_pct'), '%')}｜ATR {_fmt(r.get('atr_pct'), '%')}"
        f"｜量比 {_fmt(r.get('volume_ratio'), 'x')}"
        + (f"｜RS/加權 1月 {_fmt(r.get('rs_vs_taiex_1m'), '%')} 3月 {_fmt(r.get('rs_vs_taiex_3m'), '%')}"
           if r.get("rs_vs_taiex_1m") is not None else "")
        + (f"｜訊號: {', '.join(r['signals'])}" if r.get("signals") else "")
        + flag_str
    )


def _fund_line(f: dict) -> str:
    if "error" in f:
        return f"- {f['symbol']}: 基本面缺失"
    def pct(v):
        return "n/a" if v is None else f"{v*100:.1f}%"
    return (
        f"- {f['symbol']} ({f.get('name') or ''}, {f.get('sector') or ''}): 市值 {_fmt(f.get('market_cap'))}"
        f"｜PE {_fmt(f.get('trailing_pe'))} / 前瞻 {_fmt(f.get('forward_pe'))}｜PEG {_fmt(f.get('peg'))}"
        f"｜營收成長 {pct(f.get('revenue_growth'))}｜獲利成長 {pct(f.get('earnings_growth'))}"
        f"｜毛利率 {pct(f.get('gross_margin'))}｜營益率 {pct(f.get('operating_margin'))}"
        f"｜殖利率 {pct(f.get('dividend_yield'))}｜Beta {_fmt(f.get('beta'))}"
        f"｜分析師目標 {_fmt(f.get('analyst_target'))}｜評等 {f.get('recommendation') or 'n/a'}"
        f"｜下次財報 {f.get('next_earnings') or 'n/a'}"
    )


def _chip_line(c: dict) -> str:
    if not c or c.get("error"):
        return f"- {c.get('symbol', '?')}: 籌碼資料缺失" + (f" ({c['error']})" if c and c.get("error") else "")
    return (
        f"- {c['symbol']}: 外資買賣超 {_fmt(c.get('foreign_net_lots'))} 張｜投信買賣超 {_fmt(c.get('trust_net_lots'))} 張"
        f"｜自營商買賣超 {_fmt(c.get('dealer_net_lots'))} 張｜三大法人合計 {_fmt(c.get('total_net_lots'))} 張"
        f"｜融資餘額 {_fmt(c.get('margin_balance_lots'))} 張（{_fmt(c.get('margin_balance_chg_lots'))} 張）"
        f"｜融券餘額 {_fmt(c.get('short_balance_lots'))} 張（{_fmt(c.get('short_balance_chg_lots'))} 張）"
        f"｜資料日 {c.get('date', 'n/a')}"
    )


def format_packet(packet: MarketPacket) -> str:
    lines = [f"# 台股市場資料快照（{packet.as_of}）", "", "## 宏觀/指數"]
    lines += [_row_line(r) for r in packet.macro.values()]
    lines += ["", "## 台股 ETF"]
    for sym, r in packet.sectors.items():
        lines.append(_row_line({**r, "symbol": f"{sym} {SECTOR_ETFS.get(sym, '')}"}))
    lines += ["", "## 觀察清單技術面（日線）"]
    lines += [_row_line(r) for r in packet.stocks.values()]
    if packet.fundamentals:
        lines += ["", "## 觀察清單基本面"]
        lines += [_fund_line(f) for f in packet.fundamentals.values()]
    if packet.chips:
        lines += ["", "## 觀察清單籌碼面（三大法人買賣超 / 融資融券，單位：張）"]
        lines += [_chip_line(c) for c in packet.chips.values()]
    if packet.errors:
        lines += ["", "## 資料缺失", *[f"- {e}" for e in packet.errors]]
    return "\n".join(lines)
