"""Taiwan stock market data fetcher using TWSE official API and yfinance."""
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional
import time

TWSE_BASE = "https://www.twse.com.tw/rwd/zh"
TPEX_BASE = "https://www.tpex.org.tw/web/stock"
MIS_BASE  = "https://mis.twse.com.tw/stock/api"

# Major Taiwan indices and blue-chip stocks to track
WATCH_LIST = [
    "^TWII",     # TAIEX (加權指數)
    "2330.TW",   # TSMC
    "2317.TW",   # Hon Hai (Foxconn)
    "2454.TW",   # MediaTek
    "2412.TW",   # Chunghwa Telecom
    "2882.TW",   # Cathay Financial
    "2881.TW",   # Fubon Financial
    "2303.TW",   # United Microelectronics (UMC)
    "2308.TW",   # Delta Electronics
    "1301.TW",   # Formosa Plastics
    "2002.TW",   # China Steel
    "3008.TW",   # LARGAN Precision
    "2886.TW",   # Mega Financial
    "2891.TW",   # CTBC Financial
    "1303.TW",   # Nan Ya Plastics
]

SECTOR_MAP = {
    "2330.TW": "半導體",
    "2303.TW": "半導體",
    "2454.TW": "半導體",
    "3008.TW": "光學",
    "2317.TW": "電子製造",
    "2308.TW": "電子零件",
    "2412.TW": "電信",
    "2882.TW": "金融",
    "2881.TW": "金融",
    "2886.TW": "金融",
    "2891.TW": "金融",
    "1301.TW": "塑化",
    "1303.TW": "塑化",
    "2002.TW": "鋼鐵",
}


def _safe_get(url: str, params: dict = None, timeout: int = 10) -> Optional[dict]:
    """HTTP GET with basic retry."""
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=timeout,
                             headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                print(f"  [fetcher] 警告: GET {url} 失敗 — {e}")
            time.sleep(1)
    return None


def fetch_taiex_summary() -> dict:
    """
    Fetch TAIEX (大盤) current summary from TWSE market information system.
    Returns index value, change, and volume.
    """
    url = f"{TWSE_BASE}/index/TWT001U"
    data = _safe_get(url)
    result = {"source": "TWSE TWT001U", "fetched_at": datetime.now().isoformat()}

    if data and data.get("stat") == "OK":
        rows = data.get("data", [])
        if rows:
            # columns: 日期, 成交股數, 成交金額, 發行量加權股價指數, 漲跌點數
            latest = rows[-1]
            result.update({
                "date": latest[0],
                "volume_shares": latest[1],
                "volume_amount": latest[2],
                "taiex": latest[3],
                "change": latest[4],
            })
    return result


def fetch_market_breadth() -> dict:
    """
    Fetch market breadth (上漲/下跌家數) from TWSE.
    """
    url = f"{TWSE_BASE}/afterTrading/BWIBBU_d"
    data = _safe_get(url)
    result = {"source": "TWSE BWIBBU_d", "fetched_at": datetime.now().isoformat()}

    if data and data.get("stat") == "OK":
        rows = data.get("data", [])
        if rows:
            # Take first few rows as summary indicators (殖利率、本益比等)
            result["pe_data"] = rows[:10]
            result["fields"] = data.get("fields", [])
    return result


def fetch_top_movers() -> dict:
    """
    Fetch top gainers and losers of the day from TWSE.
    """
    today = datetime.now().strftime("%Y%m%d")
    url = f"{TWSE_BASE}/afterTrading/MI_INDEX"
    params = {"date": today, "type": "MS"}
    data = _safe_get(url, params=params)

    result = {
        "source": "TWSE MI_INDEX",
        "fetched_at": datetime.now().isoformat(),
        "gainers": [],
        "losers": [],
    }

    if data and data.get("stat") == "OK":
        tables = data.get("tables", [])
        for table in tables:
            fields = table.get("fields", [])
            rows = table.get("data", [])
            if "漲跌價差" in fields or "漲跌(%)" in fields:
                result["fields"] = fields
                result["rows"] = rows[:20]
    return result


def fetch_yfinance_data(symbols: list[str], period: str = "5d",
                        interval: str = "1h") -> dict[str, pd.DataFrame]:
    """
    Fetch OHLCV data for a list of symbols via yfinance.
    Returns a dict of {symbol: DataFrame}.
    """
    results = {}
    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            df = ticker.history(period=period, interval=interval)
            if not df.empty:
                df.index = pd.to_datetime(df.index)
                results[sym] = df
        except Exception as e:
            print(f"  [fetcher] yfinance {sym} 失敗 — {e}")
    return results


def fetch_all_watchlist(period: str = "5d", interval: str = "1h") -> dict[str, pd.DataFrame]:
    """Fetch OHLCV for the full watch list."""
    return fetch_yfinance_data(WATCH_LIST, period=period, interval=interval)


def fetch_stock_info(symbol: str) -> dict:
    """Fetch fundamental info for a single stock."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        return {
            "symbol": symbol,
            "name": info.get("longName") or info.get("shortName", symbol),
            "sector": SECTOR_MAP.get(symbol, info.get("sector", "未知")),
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "previous_close": info.get("previousClose"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
            "avg_volume": info.get("averageVolume"),
        }
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}
