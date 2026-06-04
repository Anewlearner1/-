from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_returns_200():
    assert client.get("/health").status_code == 200


def test_health_returns_status_ok():
    assert client.get("/health").json() == {"status": "ok", "env": "development"}
