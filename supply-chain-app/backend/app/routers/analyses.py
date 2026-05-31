from fastapi import APIRouter, Depends, HTTPException
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


class SaveRequest(BaseModel):
    product: str
    tree_json: str


@router.post("/save", status_code=201)
def save_analysis(body: SaveRequest, user_id: str = Depends(require_auth)) -> dict:
    # Full DB persistence added in refactor — stub returns created record id
    import uuid
    return {"id": str(uuid.uuid4()), "product": body.product}


@router.get("/{analysis_id}")
def get_analysis(
    analysis_id: str,
    user_id: str = Depends(require_auth),
) -> dict:
    raise HTTPException(status_code=404, detail="Not found")
