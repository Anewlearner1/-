import requests

from app.config import settings


class TavilyService:
    _base_url = "https://api.tavily.com/search"

    def search(self, query: str, max_results: int = 5) -> str:
        if not settings.tavily_api_key:
            return ""
        payload = {
            "api_key": settings.tavily_api_key,
            "query": f"{query} supply chain components manufacturers",
            "max_results": max_results,
            "search_depth": "basic",
        }
        try:
            response = requests.post(self._base_url, json=payload, timeout=10)
            response.raise_for_status()
            results = response.json().get("results", [])
            return "\n\n".join(
                f"Source: {r['url']}\n{r['content']}" for r in results
            )
        except Exception:
            return ""
