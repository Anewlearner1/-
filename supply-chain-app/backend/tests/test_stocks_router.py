from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MOCK_STOCK = {
    "symbol": "TSM",
    "name": "Taiwan Semiconductor Manufacturing",
    "price": 150.0,
    "market_cap": 700_000_000_000,
    "exchange": "NYSE",
    "is_small_cap": False,
}


def test_stocks_search_returns_200():
    with patch("app.routers.stocks.ClaudeService") as MockClaude, \
         patch("app.routers.stocks.StockService") as MockStock:
        mock_claude = MockClaude.return_value
        mock_claude.identify_companies = AsyncMock(return_value=["TSM", "INTC"])
        mock_stock = MockStock.return_value
        mock_stock.get_stock_info.return_value = MOCK_STOCK
        response = client.post(
            "/api/v1/stocks/search",
            json={"node_label": "TSMC N3", "node_description": "3nm foundry"},
        )
    assert response.status_code == 200


def test_stocks_search_returns_list():
    with patch("app.routers.stocks.ClaudeService") as MockClaude, \
         patch("app.routers.stocks.StockService") as MockStock:
        mock_claude = MockClaude.return_value
        mock_claude.identify_companies = AsyncMock(return_value=["TSM"])
        mock_stock = MockStock.return_value
        mock_stock.get_stock_info.return_value = MOCK_STOCK
        response = client.post(
            "/api/v1/stocks/search",
            json={"node_label": "TSMC N3", "node_description": "3nm foundry"},
        )
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0


def test_stocks_search_filters_out_none_results():
    with patch("app.routers.stocks.ClaudeService") as MockClaude, \
         patch("app.routers.stocks.StockService") as MockStock:
        mock_claude = MockClaude.return_value
        mock_claude.identify_companies = AsyncMock(return_value=["TSM", "INVALID"])
        mock_stock = MockStock.return_value
        mock_stock.get_stock_info.side_effect = lambda t: MOCK_STOCK if t == "TSM" else None
        response = client.post(
            "/api/v1/stocks/search",
            json={"node_label": "TSMC N3", "node_description": "3nm foundry"},
        )
    data = response.json()
    assert len(data) == 1
    assert data[0]["symbol"] == "TSM"


def test_stocks_search_requires_node_label():
    response = client.post("/api/v1/stocks/search", json={"node_description": "xyz"})
    assert response.status_code == 422
