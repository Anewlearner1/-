from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


MOCK_TREE = {
    "nodes": [
        {
            "id": "0",
            "label": "iPhone 15 Pro",
            "tier": 0,
            "country": "USA",
            "risk_level": "low",
            "replaceability": "high",
            "description": "Finished consumer device",
            "role": "End Product",
        },
        {
            "id": "1",
            "label": "A17 Pro SoC",
            "tier": 1,
            "country": "Taiwan",
            "risk_level": "high",
            "replaceability": "low",
            "description": "Apple-designed chip fabbed by TSMC N3",
            "role": "Processor",
        },
    ],
    "edges": [{"source": "0", "target": "1"}],
}


def test_analyze_returns_200():
    with patch("app.routers.analysis.ClaudeService") as MockClaude:
        instance = MockClaude.return_value
        instance.analyze = AsyncMock(return_value=MOCK_TREE)
        response = client.post(
            "/api/v1/analysis/analyze", json={"product": "iPhone 15 Pro"}
        )
    assert response.status_code == 200


def test_analyze_returns_nodes_and_edges():
    with patch("app.routers.analysis.ClaudeService") as MockClaude:
        instance = MockClaude.return_value
        instance.analyze = AsyncMock(return_value=MOCK_TREE)
        response = client.post(
            "/api/v1/analysis/analyze", json={"product": "iPhone 15 Pro"}
        )
    data = response.json()
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) > 0


def test_analyze_requires_product_field():
    response = client.post("/api/v1/analysis/analyze", json={})
    assert response.status_code == 422


def test_analyze_rejects_empty_product():
    response = client.post("/api/v1/analysis/analyze", json={"product": "  "})
    assert response.status_code == 422


def test_node_has_all_required_fields():
    with patch("app.routers.analysis.ClaudeService") as MockClaude:
        instance = MockClaude.return_value
        instance.analyze = AsyncMock(return_value=MOCK_TREE)
        response = client.post(
            "/api/v1/analysis/analyze", json={"product": "iPhone 15 Pro"}
        )
    node = response.json()["nodes"][0]
    for field in ("id", "label", "tier", "country", "risk_level", "replaceability", "description", "role"):
        assert field in node, f"missing field: {field}"


def test_edge_has_source_and_target():
    with patch("app.routers.analysis.ClaudeService") as MockClaude:
        instance = MockClaude.return_value
        instance.analyze = AsyncMock(return_value=MOCK_TREE)
        response = client.post(
            "/api/v1/analysis/analyze", json={"product": "iPhone 15 Pro"}
        )
    edge = response.json()["edges"][0]
    assert "source" in edge
    assert "target" in edge
