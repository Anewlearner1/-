"""Upload API endpoint (FastAPI) wrapping `backend.upload_quality`.

This is the "upload API endpoint" that `design/upload-flow.md` §5 flags as
an open question for backend-engineer: it exposes `check_upload_quality()`
over HTTP as JSON, using the *same* field names ui-ux-designer already
documented from `QualityReport` / `CheckResult` (`passed`, `checks[].status`,
`checks[].message_zh`, `messages_zh`, ...) -- nothing is renamed.

Scope note: passed uploads get a real SQLite `uploads` row (backend/db.py)
with status "queued" and its id as `upload_id`. A separate process,
`python -m backend.worker`, moves rows to processing/done/failed and fills the
shots; this API does not start it, so with no worker running a row stays
"queued". Failed-gate uploads create no row. Also:
GET /uploads/{id} and GET /uploads/{id}/shots (404 -> {"error":
"upload_not_found"}).

Two distinct response shapes, on purpose (see design doc §4):
  1. A completed quality check (regardless of pass/fail) -> HTTP 200,
     JSON shaped exactly like `QualityReport` (`to_dict()` below), plus
     `upload_id` when `passed` is True.
  2. A malformed/unreadable file (`ValueError` from `check_upload_quality`,
     i.e. OpenCV couldn't open it) -> HTTP 422 with a *different* shape
     (`{"error": "invalid_video_file", "detail": ...}`), so the frontend
     can tell "re-shoot, your footage doesn't meet a check" apart from
     "this isn't a readable video file at all" per design doc §4.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend import db
from backend.upload_quality import (
    CheckResult,
    QualityReport,
    check_upload_quality,
)

# Configurable upload directory. Kept as a module-level path (not a real
# settings/config system -- none exists in this repo yet) so tests can
# monkeypatch it if needed.
UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"

app = FastAPI(title="Rally AI upload API")


def _check_result_to_dict(check: CheckResult) -> dict:
    """Serializes a CheckResult with the exact field names
    design/upload-flow.md documents: name, status, message_zh, detail,
    metrics. `status` is a CheckStatus str-enum, so `.value` gives the
    plain "pass"/"fail"/"not_implemented" string the frontend expects.
    """
    return {
        "name": check.name,
        "status": check.status.value,
        "message_zh": check.message_zh,
        "detail": check.detail,
        "metrics": check.metrics,
    }


def _quality_report_to_dict(report: QualityReport) -> dict:
    """Serializes a QualityReport with the exact field names
    design/upload-flow.md §0 documents: passed, checks[], messages_zh.
    `video_path` is included too since it's already a real field on the
    dataclass; it isn't part of the documented UI contract but costs
    nothing to include and is useful for debugging.
    """
    return {
        "video_path": report.video_path,
        "passed": report.passed,
        "checks": [_check_result_to_dict(c) for c in report.checks],
        "messages_zh": report.messages_zh,
    }


def _enqueue_upload_record(original_filename, stored_path: str, report: dict,
                           racket_hand: Optional[str] = None) -> str:
    """Persist a passed upload as a real `uploads` row with status "queued".

    The row is real. Processing happens only if `python -m backend.worker` is
    running (this API does not start it); otherwise the status stays "queued".
    """
    return db.create_upload(original_filename, stored_path, report, status="queued",
                            racket_hand=racket_hand)


@app.post("/upload")
async def upload_video(file: UploadFile = File(...),
                       racket_hand: Optional[str] = Form(None)) -> JSONResponse:
    """Accepts an uploaded video, runs the upload-time quality gate, and
    returns a JSON QualityReport (plus an upload_id stub if it passed).

    ``racket_hand`` (optional form field, "left" or "right") is stored with
    the upload; the worker uses it for forehand/backhand because inferring the
    hand from motion failed on 5 of 7 real clips. Anything else -> 422
    {"error": "invalid_racket_hand"} before the file is stored.

    See module docstring for the two distinct response shapes.
    """
    if racket_hand == "":
        racket_hand = None
    if racket_hand is not None and racket_hand not in db.RACKET_HANDS:
        await file.close()
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_racket_hand",
                     "detail": f"racket_hand must be one of {list(db.RACKET_HANDS)}"},
        )
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # Keep the original suffix (if any) so OpenCV's container sniffing has
    # something to go on; prefix with a random id to avoid collisions.
    suffix = Path(file.filename or "").suffix
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"

    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    await file.close()

    try:
        report = check_upload_quality(dest)
    except FileNotFoundError as exc:
        # Shouldn't happen in practice since we just wrote `dest` ourselves,
        # but handled defensively and kept distinct from the ValueError
        # case per design doc §4's "different frontend handling" note.
        return JSONResponse(
            status_code=404,
            content={"error": "file_not_found", "detail": str(exc)},
        )
    except ValueError as exc:
        # design/upload-flow.md §4: "檔案無法開啟 / 非影片格式" -- a
        # different error shape than a QualityReport FAIL, so the frontend
        # can show a generic "unsupported/corrupt file" message instead of
        # the re-shoot-guidance UI in §3b.
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_video_file", "detail": str(exc)},
        )

    body = _quality_report_to_dict(report)
    if report.passed:
        # Real DB row, but no worker consumes it -- see docstring.
        body["upload_id"] = _enqueue_upload_record(file.filename, str(dest), body,
                                                   racket_hand=racket_hand)
    # Failed-gate uploads: NO row is created (status set has no "rejected";
    # the report is returned to the client, who must re-shoot).

    return JSONResponse(status_code=200, content=body)


def _not_found(upload_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "upload_not_found",
                 "detail": f"no upload with id {upload_id!r}"},
    )


@app.get("/uploads/{upload_id}")
async def get_upload_status(upload_id: str) -> JSONResponse:
    """Status + stored quality report. Status stays "queued" until a worker runs."""
    row = db.get_upload(upload_id)
    if row is None:
        return _not_found(upload_id)
    return JSONResponse(status_code=200, content=row)


@app.get("/uploads/{upload_id}/shots")
async def get_upload_shots(upload_id: str) -> JSONResponse:
    """Per-shot data ordered by shot_index. fh_bh_label / ball_speed_kmh are
    JSON null when not analyzed (dashboard "尚未分析"), never 0. An existing
    upload with no shots yet returns shot_count 0 and shots [].
    """
    if db.get_upload(upload_id) is None:
        return _not_found(upload_id)
    shots = db.list_shots(upload_id)
    return JSONResponse(
        status_code=200,
        content={"upload_id": upload_id, "shot_count": len(shots), "shots": shots},
    )


_VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
                ".webm": "video/webm", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo"}


@app.get("/uploads/{upload_id}/video")
async def get_upload_video(upload_id: str):
    """The stored video, for dashboard playback (M6). Starlette's FileResponse
    answers HTTP Range requests, so the browser can seek. The path comes from
    the DB row the API itself wrote, never from the request. 404
    upload_not_found if the id or the stored file is missing."""
    path = db.stored_path(upload_id)
    if path is None or not Path(path).is_file():
        return _not_found(upload_id)
    media = _VIDEO_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media)


# The static frontend, served from the same origin as the API so the pages
# work with no CORS setup: http://localhost:8000/app/upload.html and
# /app/dashboard.html?upload=<id>. Mounted last so it cannot shadow API routes.
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/app/upload.html")


if FRONTEND_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
