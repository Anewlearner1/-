import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.claude_service import ClaudeService

VALID_TREE = {
    "nodes": [
        {
            "id": "0",
            "label": "HBM",
            "tier": 0,
            "country": "South Korea",
            "risk_level": "high",
            "replaceability": "low",
            "description": "High Bandwidth Memory",
            "role": "Memory",
        }
    ],
    "edges": [],
}


def _mock_client(text: str) -> MagicMock:
    content_block = MagicMock()
    content_block.text = text
    message = MagicMock()
    message.content = [content_block]
    client = MagicMock()
    client.messages.create.return_value = message
    return client


@pytest.mark.asyncio
async def test_analyze_parses_valid_json():
    with patch("app.services.claude_service.anthropic.Anthropic") as MockAnthropic:
        MockAnthropic.return_value = _mock_client(json.dumps(VALID_TREE))
        result = await ClaudeService().analyze("HBM")
    assert result["nodes"][0]["label"] == "HBM"


@pytest.mark.asyncio
async def test_analyze_strips_markdown_fences():
    raw = f"```json\n{json.dumps(VALID_TREE)}\n```"
    with patch("app.services.claude_service.anthropic.Anthropic") as MockAnthropic:
        MockAnthropic.return_value = _mock_client(raw)
        result = await ClaudeService().analyze("HBM")
    assert "nodes" in result


@pytest.mark.asyncio
async def test_analyze_raises_on_invalid_json():
    with patch("app.services.claude_service.anthropic.Anthropic") as MockAnthropic:
        MockAnthropic.return_value = _mock_client("not json at all")
        with pytest.raises(Exception):
            await ClaudeService().analyze("HBM")
