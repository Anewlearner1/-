from fastapi import APIRouter, HTTPException

from app.schemas.analysis import AnalysisRequest, AnalysisResponse
from app.services.claude_service import ClaudeService
from app.services.tavily_service import TavilyService

router = APIRouter(prefix="/api/v1/analysis", tags=["analysis"])


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze(request: AnalysisRequest) -> AnalysisResponse:
    tavily = TavilyService()
    context = tavily.search(request.product)

    claude = ClaudeService()
    try:
        data = await claude.analyze(request.product, context)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Analysis failed: {exc}") from exc

    return AnalysisResponse(**data)
