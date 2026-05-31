from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import crud
from app.config import settings
from app.database import get_db

router = APIRouter(prefix="/api/v1/analyses", tags=["analyses"])

_bearer = HTTPBearer(auto_error=False)


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.nextauth_secret,
            algorithms=["HS256"],
        )
        user_id: str | None = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


class SaveRequest(BaseModel):
    product: str
    tree_json: str


@router.post("/save", status_code=201)
def save_analysis(
    body: SaveRequest,
    user_id: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict:
    record = crud.save_analysis(db, user_id, body.product, body.tree_json)
    return {"id": record.id, "product": record.product}


@router.get("/history")
def list_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return crud.get_user_analyses(db, user_id, page=page, page_size=page_size)


@router.get("/{analysis_id}")
def get_analysis(
    analysis_id: str,
    user_id: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict:
    record = crud.get_analysis(db, analysis_id, user_id)
    if not record:
        raise HTTPException(status_code=404, detail="Not found")
    return {
        "id": record.id,
        "product": record.product,
        "tree_json": record.tree_json,
        "created_at": record.created_at.isoformat(),
    }


@router.delete("/{analysis_id}", status_code=204)
def delete_analysis_endpoint(
    analysis_id: str,
    user_id: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> Response:
    crud.delete_user_analysis(db, analysis_id, user_id)
    return Response(status_code=204)
