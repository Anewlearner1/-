"""Minimal SQLite persistence for uploads and per-shot data (stdlib only).

Honest status: this is storage only. There is NO worker/queue consumer in
this repo yet, so an upload row stays at status "queued" forever, and the
`shots` table is only filled by `insert_shots_from_result()`, which nothing
calls in production yet (a future worker is expected to).

NULL semantics: `stroke_label` (FH/BH, M3) and `ball_speed_kmh` (M5) are
nullable. NULL means "not analyzed yet" -> the dashboard's "尚未分析" state
(design/dashboard.md §3). Never store 0 / "" as a stand-in.

DB path: env var RALLY_DB_PATH, default <repo>/data/rally.sqlite3. It is
resolved on every call so tests can point it at a temp file.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DB_ENV_VAR = "RALLY_DB_PATH"
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "rally.sqlite3"

UPLOAD_STATUSES = ("queued", "processing", "done", "failed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY,
    original_filename TEXT,
    stored_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued','processing','done','failed')),
    quality_report_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id TEXT NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    shot_index INTEGER NOT NULL,
    contact_frame INTEGER NOT NULL,
    contact_time_s REAL NOT NULL,
    peak_speed REAL NOT NULL,          -- wrist speed, NOT ball speed
    wrist TEXT,                        -- "left"/"right" (dashboard contract)
    stroke_label TEXT,                 -- NULL until M3 classifier has run
    ball_speed_kmh REAL,               -- NULL until M5 exists
    source TEXT NOT NULL,              -- provenance, e.g. "ml.shot_timing.detect_shots"
    UNIQUE (upload_id, shot_index)
);
"""


def db_path() -> Path:
    return Path(os.environ.get(DB_ENV_VAR) or DEFAULT_DB_PATH)


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def create_upload(original_filename: Optional[str], stored_path: str,
                  quality_report: dict, status: str = "queued") -> str:
    if status not in UPLOAD_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    upload_id = uuid.uuid4().hex
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT INTO uploads (id, original_filename, stored_path, created_at,"
            " status, quality_report_json) VALUES (?,?,?,?,?,?)",
            (upload_id, original_filename, stored_path,
             datetime.now(timezone.utc).isoformat(), status,
             json.dumps(quality_report, ensure_ascii=False)),
        )
    return upload_id


def get_upload(upload_id: str) -> Optional[dict]:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
    if row is None:
        return None
    return {
        "upload_id": row["id"],
        "original_filename": row["original_filename"],
        "created_at": row["created_at"],
        "status": row["status"],
        "quality_report": json.loads(row["quality_report_json"]),
    }


def set_upload_status(upload_id: str, status: str) -> None:
    if status not in UPLOAD_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE uploads SET status = ? WHERE id = ?", (status, upload_id))


def insert_shots_from_result(upload_id: str, result: Any,
                             source: str = "ml.shot_timing.detect_shots") -> int:
    """Store the events of an `ml.shot_timing.ShotTimingResult` for an upload.

    Intended for a future worker; nothing calls it in production today.
    Replaces any existing shots for the upload. stroke_label and
    ball_speed_kmh are left NULL (not available from shot timing).
    Returns the number of shots inserted.
    """
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM shots WHERE upload_id = ?", (upload_id,))
        conn.executemany(
            "INSERT INTO shots (upload_id, shot_index, contact_frame, contact_time_s,"
            " peak_speed, wrist, stroke_label, ball_speed_kmh, source)"
            " VALUES (?,?,?,?,?,?,NULL,NULL,?)",
            [(upload_id, i, int(e.contact_frame), float(e.contact_time_s),
              float(e.peak_speed), e.wrist, source)
             for i, e in enumerate(result.events)],
        )
    return len(result.events)


def list_shots(upload_id: str) -> list[dict]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM shots WHERE upload_id = ? ORDER BY shot_index", (upload_id,)
        ).fetchall()
    return [
        {
            "shot_index": r["shot_index"],
            "contact_frame": r["contact_frame"],
            "contact_time_s": r["contact_time_s"],
            "peak_speed": r["peak_speed"],
            "wrist": r["wrist"],
            "fh_bh_label": r["stroke_label"],      # None -> JSON null -> 尚未分析
            "ball_speed_kmh": r["ball_speed_kmh"],  # None -> JSON null -> 尚未分析
            "source": r["source"],
        }
        for r in rows
    ]
