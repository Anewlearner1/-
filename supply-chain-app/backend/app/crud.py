import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.analysis import AnalysisResult


def save_analysis(db: Session, user_id: str, product: str, tree_json: str) -> AnalysisResult:
    record = AnalysisResult(
        id=str(uuid.uuid4()),
        user_id=user_id,
        product=product,
        tree_json=tree_json,
        created_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_user_analyses(
    db: Session, user_id: str, page: int = 1, page_size: int = 20
) -> list[dict]:
    offset = (page - 1) * page_size
    rows = (
        db.query(AnalysisResult)
        .filter(AnalysisResult.user_id == user_id)
        .order_by(AnalysisResult.created_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    return [
        {
            "id": r.id,
            "product": r.product,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


def get_analysis(db: Session, analysis_id: str, user_id: str) -> AnalysisResult | None:
    return (
        db.query(AnalysisResult)
        .filter(AnalysisResult.id == analysis_id, AnalysisResult.user_id == user_id)
        .first()
    )


def delete_user_analysis(db: Session, analysis_id: str, user_id: str) -> bool:
    record = get_analysis(db, analysis_id, user_id)
    if not record:
        return False
    db.delete(record)
    db.commit()
    return True
