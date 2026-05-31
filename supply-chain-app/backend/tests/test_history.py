from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_history_requires_auth():
    response = client.get("/api/v1/analyses/history")
    assert response.status_code == 401


def test_history_returns_list():
    with patch("app.routers.analyses.require_auth", return_value="user-1"), \
         patch("app.routers.analyses.get_user_analyses") as mock_list:
        mock_list.return_value = [
            {"id": "a1", "product": "iPhone 15", "created_at": "2026-01-01T00:00:00Z"},
        ]
        response = client.get(
            "/api/v1/analyses/history",
            headers={"Authorization": "Bearer token"},
        )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert data[0]["product"] == "iPhone 15"


def test_history_supports_pagination():
    with patch("app.routers.analyses.require_auth", return_value="user-1"), \
         patch("app.routers.analyses.get_user_analyses") as mock_list:
        mock_list.return_value = []
        response = client.get(
            "/api/v1/analyses/history?page=2&page_size=5",
            headers={"Authorization": "Bearer token"},
        )
    assert response.status_code == 200
    mock_list.assert_called_once_with("token", page=2, page_size=5)


def test_history_delete_requires_auth():
    response = client.delete("/api/v1/analyses/abc123")
    assert response.status_code == 401


def test_history_delete_returns_204():
    with patch("app.routers.analyses.require_auth", return_value="user-1"), \
         patch("app.routers.analyses.delete_analysis") as mock_del:
        mock_del.return_value = True
        response = client.delete(
            "/api/v1/analyses/abc123",
            headers={"Authorization": "Bearer token"},
        )
    assert response.status_code == 204
