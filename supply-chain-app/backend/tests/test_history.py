from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.analysis import Base
from app.routers.analyses import require_auth
from app.database import get_db

TEST_DB = "sqlite:///:memory:"


@pytest.fixture(autouse=True)
def override_db():
    engine = create_engine(TEST_DB, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def _get_test_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_test_db
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)


@pytest.fixture
def authed_client():
    app.dependency_overrides[require_auth] = lambda: "test-user-id"
    client = TestClient(app)
    yield client
    app.dependency_overrides.pop(require_auth, None)


client = TestClient(app)


def test_history_requires_auth():
    response = client.get("/api/v1/analyses/history")
    assert response.status_code == 401


def test_history_returns_list(authed_client):
    with patch("app.crud.get_user_analyses") as mock_list:
        mock_list.return_value = [
            {"id": "a1", "product": "iPhone 15", "created_at": "2026-01-01T00:00:00Z"},
        ]
        response = authed_client.get("/api/v1/analyses/history")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert data[0]["product"] == "iPhone 15"


def test_history_supports_pagination(authed_client):
    with patch("app.crud.get_user_analyses") as mock_list:
        mock_list.return_value = []
        response = authed_client.get("/api/v1/analyses/history?page=2&page_size=5")
    assert response.status_code == 200
    mock_list.assert_called_once()
    _, kwargs = mock_list.call_args
    assert kwargs.get("page") == 2
    assert kwargs.get("page_size") == 5


def test_history_delete_requires_auth():
    response = client.delete("/api/v1/analyses/abc123")
    assert response.status_code == 401


def test_history_delete_returns_204(authed_client):
    with patch("app.crud.delete_user_analysis") as mock_del:
        mock_del.return_value = True
        response = authed_client.delete("/api/v1/analyses/abc123")
    assert response.status_code == 204
