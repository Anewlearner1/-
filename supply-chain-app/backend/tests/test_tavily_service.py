from unittest.mock import MagicMock, patch

from app.services.tavily_service import TavilyService


def test_search_returns_empty_string_when_no_api_key(monkeypatch):
    monkeypatch.setattr("app.services.tavily_service.settings.tavily_api_key", "")
    result = TavilyService().search("HBM memory")
    assert result == ""


def test_search_returns_concatenated_content(monkeypatch):
    monkeypatch.setattr(
        "app.services.tavily_service.settings.tavily_api_key", "fake-key"
    )
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "results": [
            {"url": "https://example.com/a", "content": "SK Hynix makes HBM"},
            {"url": "https://example.com/b", "content": "Micron also produces HBM"},
        ]
    }
    mock_response.raise_for_status = MagicMock()
    with patch("app.services.tavily_service.requests.post", return_value=mock_response):
        result = TavilyService().search("HBM memory")
    assert "SK Hynix" in result
    assert "Micron" in result


def test_search_returns_empty_on_network_error(monkeypatch):
    monkeypatch.setattr(
        "app.services.tavily_service.settings.tavily_api_key", "fake-key"
    )
    with patch(
        "app.services.tavily_service.requests.post",
        side_effect=Exception("timeout"),
    ):
        result = TavilyService().search("HBM memory")
    assert result == ""
