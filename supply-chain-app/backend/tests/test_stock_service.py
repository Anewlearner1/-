from unittest.mock import MagicMock, patch

import pytest

from app.services import stock_service
from app.services.stock_service import StockService


@pytest.fixture(autouse=True)
def clear_cache():
    stock_service._price_cache.clear()
    yield
    stock_service._price_cache.clear()


@pytest.fixture
def service():
    return StockService()


def test_get_stock_info_returns_expected_keys(service):
    mock_info = {
        "shortName": "Taiwan Semiconductor Manufacturing",
        "symbol": "TSM",
        "currentPrice": 150.0,
        "marketCap": 700_000_000_000,
        "exchange": "NYSE",
    }
    with patch("app.services.stock_service.yf.Ticker") as MockTicker:
        MockTicker.return_value.info = mock_info
        result = service.get_stock_info("TSM")
    assert result is not None
    assert result["symbol"] == "TSM"
    assert result["name"] == "Taiwan Semiconductor Manufacturing"
    assert result["price"] == 150.0
    assert result["market_cap"] == 700_000_000_000
    assert result["is_small_cap"] is False


def test_get_stock_info_marks_small_cap(service):
    mock_info = {
        "shortName": "Tiny Co",
        "symbol": "TINY",
        "currentPrice": 5.0,
        "marketCap": 500_000_000,  # $500M < $2B threshold
        "exchange": "NASDAQ",
    }
    with patch("app.services.stock_service.yf.Ticker") as MockTicker:
        MockTicker.return_value.info = mock_info
        result = service.get_stock_info("TINY")
    assert result is not None
    assert result["is_small_cap"] is True


def test_get_stock_info_returns_none_for_unknown_ticker(service):
    with patch("app.services.stock_service.yf.Ticker") as MockTicker:
        MockTicker.return_value.info = {}
        result = service.get_stock_info("INVALID_XYZ")
    assert result is None


def test_get_stock_info_returns_none_on_exception(service):
    with patch("app.services.stock_service.yf.Ticker", side_effect=Exception("network")):
        result = service.get_stock_info("TSM")
    assert result is None


def test_search_by_name_returns_list(service, monkeypatch):
    monkeypatch.setattr("app.services.stock_service.settings.alpha_vantage_api_key", "fake-key")
    mock_av_response = {
        "bestMatches": [
            {"1. symbol": "AAPL", "2. name": "Apple Inc.", "4. region": "United States"},
            {"1. symbol": "AAPL.LON", "2. name": "Apple Inc.", "4. region": "United Kingdom"},
        ]
    }
    with patch("app.services.stock_service.requests.get") as mock_get:
        mock_get.return_value.json.return_value = mock_av_response
        mock_get.return_value.raise_for_status = MagicMock()
        results = service.search_by_name("Apple")
    assert len(results) == 2
    assert results[0]["symbol"] == "AAPL"
    assert results[0]["name"] == "Apple Inc."


def test_search_by_name_returns_empty_on_error(service):
    with patch("app.services.stock_service.requests.get", side_effect=Exception("timeout")):
        results = service.search_by_name("Apple")
    assert results == []


def test_search_by_name_returns_empty_without_api_key(service, monkeypatch):
    monkeypatch.setattr("app.services.stock_service.settings.alpha_vantage_api_key", "")
    results = service.search_by_name("Apple")
    assert results == []
