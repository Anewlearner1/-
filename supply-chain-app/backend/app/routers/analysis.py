from fastapi import APIRouter, Depends, HTTPException

from app.schemas.analysis import AnalysisRequest, AnalysisResponse
from app.services.claude_service import ClaudeService
from app.services.tavily_service import TavilyService

router = APIRouter(prefix="/api/v1/analysis", tags=["analysis"])


def get_tavily() -> TavilyService:
    return TavilyService()


def get_claude() -> ClaudeService:
    return ClaudeService()


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze(
    request: AnalysisRequest,
    tavily: TavilyService = Depends(get_tavily),
    claude: ClaudeService = Depends(get_claude),
) -> AnalysisResponse:
    context = tavily.search(request.product)
    try:
        data = await claude.analyze(request.product, context)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Analysis failed: {exc}") from exc
    return AnalysisResponse(**data)
