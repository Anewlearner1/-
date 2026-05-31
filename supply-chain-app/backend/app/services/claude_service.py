import json
import re
import time

import anthropic

from app.config import settings
from app.schemas.analysis import AnalysisResponse

SYSTEM_PROMPT = """You are a supply chain intelligence analyst.
Given a product name, decompose its supply chain into a tree structure.
Return ONLY valid JSON (no markdown, no explanation) matching this schema:
{
  "nodes": [
    {
      "id": "string",
      "label": "string",
      "tier": 0,
      "country": "string",
      "risk_level": "low|medium|high",
      "replaceability": "low|medium|high",
      "description": "string",
      "role": "string"
    }
  ],
  "edges": [{"source": "string", "target": "string"}]
}
Tier 0 = finished product. Tier 1 = direct components. Tier 2 = subcomponents. Tier 3 = raw materials.
risk_level reflects geopolitical concentration risk. replaceability reflects how easily this node can be substituted.
"""


class ClaudeService:
    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    async def analyze(self, product: str, context: str = "") -> dict:
        user_content = f"Analyze the supply chain for: {product}"
        if context:
            user_content += f"\n\nAdditional context from recent research:\n{context}"

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                message = self._client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_content}],
                )
                raw = message.content[0].text
                raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
                raw = re.sub(r"\s*```$", "", raw.strip())
                data = json.loads(raw)
                AnalysisResponse(**data)
                return data
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)

        raise last_exc  # type: ignore[misc]
