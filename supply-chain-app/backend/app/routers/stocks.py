from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.services.claude_service import ClaudeService
from app.services.stock_service import StockService

router = APIRouter(prefix="/api/v1/stocks", tags=["stocks"])


class StockSearchRequest(BaseModel):
    node_label: str
    node_description: str = ""


def get_claude() -> ClaudeService:
    return ClaudeService()


def get_stock_service() -> StockService:
    return StockService()


@router.post("/search")
async def search_stocks(
    body: StockSearchRequest,
    claude: ClaudeService = Depends(get_claude),
    stock_svc: StockService = Depends(get_stock_service),
) -> list[dict]:
    tickers = await claude.identify_companies(body.node_label, body.node_description)
    results = []
    for ticker in tickers:
        info = stock_svc.get_stock_info(ticker)
        if info:
            results.append(info)
    return results
