from fastapi import APIRouter
from pydantic import BaseModel

from app.services.claude_service import ClaudeService
from app.services.stock_service import StockService

router = APIRouter(prefix="/api/v1/stocks", tags=["stocks"])


class StockSearchRequest(BaseModel):
    node_label: str
    node_description: str = ""


@router.post("/search")
async def search_stocks(body: StockSearchRequest) -> list[dict]:
    claude = ClaudeService()
    tickers = await claude.identify_companies(body.node_label, body.node_description)

    stock_svc = StockService()
    results = []
    for ticker in tickers:
        info = stock_svc.get_stock_info(ticker)
        if info:
            results.append(info)

    return results
