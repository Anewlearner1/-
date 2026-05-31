from unittest.mock import patch, MagicMock

from app.services import stock_service
from app.services.stock_service import StockService

MOCK_INFO = {
    "symbol": "TSM",
    "shortName": "Taiwan Semi",
    "currentPrice": 150.0,
    "marketCap": 700_000_000_000,
    "exchange": "NYSE",
}


def test_second_call_uses_cache():
    stock_service._price_cache.clear()
    with patch("app.services.stock_service.yf.Ticker") as MockTicker:
        MockTicker.return_value.info = MOCK_INFO
        svc = StockService()
        svc.get_stock_info("TSM")
        svc.get_stock_info("TSM")
        assert MockTicker.call_count == 1  # yfinance called only once


def test_expired_cache_fetches_again():
    stock_service._price_cache.clear()
    with patch("app.services.stock_service.yf.Ticker") as MockTicker, \
         patch("app.services.stock_service.time.time") as mock_time:
        MockTicker.return_value.info = MOCK_INFO
        svc = StockService()
        mock_time.return_value = 0
        svc.get_stock_info("TSM")
        mock_time.return_value = 400  # past 5-minute TTL
        svc.get_stock_info("TSM")
        assert MockTicker.call_count == 2
