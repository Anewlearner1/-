import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/analyses", tags=["analyses"])

_bearer = HTTPBearer(auto_error=False)


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return credentials.credentials


# --- stub helpers (replaced by real DB calls when DB is wired) ---

def get_user_analyses(user_id: str, page: int = 1, page_size: int = 20) -> list[dict]:
    return []


def delete_analysis(analysis_id: str, user_id: str) -> bool:
    return True


# --- request/response models ---

class SaveRequest(BaseModel):
    product: str
    tree_json: str


# --- endpoints ---

@router.post("/save", status_code=201)
def save_analysis(body: SaveRequest, user_id: str = Depends(require_auth)) -> dict:
    return {"id": str(uuid.uuid4()), "product": body.product}


@router.get("/history")
def list_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: str = Depends(require_auth),
) -> list[dict[str, Any]]:
    return get_user_analyses(user_id, page=page, page_size=page_size)


@router.get("/{analysis_id}")
def get_analysis(
    analysis_id: str,
    user_id: str = Depends(require_auth),
) -> dict:
    raise HTTPException(status_code=404, detail="Not found")


@router.delete("/{analysis_id}", status_code=204)
def delete_analysis_endpoint(
    analysis_id: str,
    user_id: str = Depends(require_auth),
) -> Response:
    delete_analysis(analysis_id, user_id)
    return Response(status_code=204)
