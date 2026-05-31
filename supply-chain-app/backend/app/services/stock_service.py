import time
from typing import Any

import requests
import yfinance as yf

from app.config import settings

SMALL_CAP_THRESHOLD = 2_000_000_000  # $2B USD
CACHE_TTL_SECONDS = 300  # 5 minutes

_price_cache: dict[str, tuple[dict, float]] = {}


def _cached_get(ticker: str) -> dict | None:
    now = time.time()
    if ticker in _price_cache:
        data, ts = _price_cache[ticker]
        if now - ts < CACHE_TTL_SECONDS:
            return data
    info = yf.Ticker(ticker).info
    if not info or not info.get("symbol"):
        return None
    _price_cache[ticker] = (info, now)
    return info


class StockService:
    def get_stock_info(self, ticker: str) -> dict[str, Any] | None:
        try:
            info = _cached_get(ticker)
            if not info:
                return None
            market_cap = info.get("marketCap") or 0
            return {
                "symbol": info.get("symbol", ticker),
                "name": info.get("shortName") or info.get("longName") or ticker,
                "price": info.get("currentPrice") or info.get("regularMarketPrice") or 0.0,
                "market_cap": market_cap,
                "exchange": info.get("exchange") or info.get("fullExchangeName") or "",
                "is_small_cap": 0 < market_cap < SMALL_CAP_THRESHOLD,
            }
        except Exception:
            return None

    def search_by_name(self, name: str) -> list[dict[str, str]]:
        if not settings.alpha_vantage_api_key:
            return []
        try:
            resp = requests.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "SYMBOL_SEARCH",
                    "keywords": name,
                    "apikey": settings.alpha_vantage_api_key,
                },
                timeout=10,
            )
            resp.raise_for_status()
            matches = resp.json().get("bestMatches", [])
            return [
                {
                    "symbol": m["1. symbol"],
                    "name": m["2. name"],
                    "region": m["4. region"],
                }
                for m in matches
            ]
        except Exception:
            return []
