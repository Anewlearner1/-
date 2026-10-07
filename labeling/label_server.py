"""Owner labeling page: mark every racket-ball contact in OpenCV decoder frames.

Run::

    python -m labeling.label_server [--port 8001] [--host 127.0.0.1]

then open http://localhost:8001/. Videos come from ``backend.library``'s
folder (``RALLY_VIDEO_DIR``, default ``data/videos/``). The page plays a
proxy (``labeling/proxy.py``) whose frames carry a barcode of their decoder
frame number, and reads the frame number from the pixels, never from
``video.currentTime``.

Saved labels go to ``labeling/labels/<stem>.json`` (override the folder with
``RALLY_LABEL_DIR``) in the schema ``ml/eval_shot_timing.load_label`` and
``ml/eval_stroke_classification`` read, plus a ``<stem>.meta.json``
provenance file. A label being replaced is first copied to
``data/label_backups/`` (gitignored).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import shutil
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel

from backend import library
from labeling import proxy

HERE = Path(__file__).resolve().parent
LABEL_DIR_ENV = "RALLY_LABEL_DIR"
DEFAULT_LABEL_DIR = HERE / "labels"
BACKUP_DIR = HERE.parent / "data" / "label_backups"
STROKES = ("forehand", "backhand", "other")


def labels_dir() -> Path:
    path = Path(os.environ.get(LABEL_DIR_ENV) or DEFAULT_LABEL_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _label_paths(stem: str) -> tuple[Path, Path]:
    d = labels_dir()
    return d / f"{stem}.json", d / f"{stem}.meta.json"


def _resolve(name: str) -> Path:
    try:
        return library.resolve_video(name)
    except ValueError:
        raise HTTPException(400, "invalid video name")
    except FileNotFoundError:
        raise HTTPException(404, "no such video")


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


class Mark(BaseModel):
    frame: int
    stroke: Literal["forehand", "backhand", "other"]


class LabelIn(BaseModel):
    fps: float
    marks: list[Mark]
    complete: bool
    racket_hand: Literal["right", "left"]


app = FastAPI(title="Rally AI labeling page")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(HERE / "label.html", media_type="text/html")


@app.get("/label.js", include_in_schema=False)
def page_js():
    return FileResponse(HERE / "label.js", media_type="text/javascript")


@app.get("/label_logic.js", include_in_schema=False)
def logic_js():
    return FileResponse(HERE / "label_logic.js", media_type="text/javascript")


@app.get("/api/videos")
def videos():
    out = []
    for p in library.list_videos():
        label_path, meta_path = _label_paths(p.stem)
        label, meta = _read_json(label_path), _read_json(meta_path) or {}
        out.append({
            "name": p.name,
            "has_label": label is not None,
            "n_contacts": len(label["contact_frames"]) if label else 0,
            "complete": bool(meta.get("complete")) if label else False,
            "frame_numbering": meta.get("frame_numbering"),
        })
    return {"video_dir": str(library.video_dir()), "videos": out}


@app.get("/api/videos/{name}/info")
def video_info(name: str):
    _, info = proxy.cached_proxy(_resolve(name))
    return info


@app.get("/api/videos/{name}/proxy")
def video_proxy(name: str):
    webm, info = proxy.cached_proxy(_resolve(name))
    if info.get("mode") == "frames":
        raise HTTPException(409, "this proxy is frame-by-frame; use /frame/{n}")
    return FileResponse(webm, media_type="video/webm")


@app.get("/api/videos/{name}/frame/{n}")
def video_frame(name: str, n: int):
    """One decoder frame as JPEG (barcode included) -- the fallback display path
    when VP8 is unavailable; works for either proxy mode."""
    import cv2
    path, info = proxy.cached_proxy(_resolve(name))
    if not 0 <= n < info["frame_count"]:
        raise HTTPException(404, f"frame out of range 0..{info['frame_count'] - 1}")
    ok, buf = cv2.imencode(".jpg", proxy.read_proxy_frame(path, n), [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise HTTPException(500, "could not encode frame")
    return Response(buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/videos/{name}/label")
def get_label(name: str):
    path = _resolve(name)
    label_path, meta_path = _label_paths(path.stem)
    label = _read_json(label_path)
    if label is None:
        raise HTTPException(404, "no label yet")
    return {"label": label, "meta": _read_json(meta_path) or {}}


@app.post("/api/videos/{name}/label")
def save_label(name: str, body: LabelIn):
    path = _resolve(name)
    _, info = proxy.cached_proxy(path)
    if not math.isclose(body.fps, info["fps"], rel_tol=1e-3):
        raise HTTPException(422, f"fps {body.fps} does not match the decoder's {info['fps']}")
    frames = [m.frame for m in body.marks]
    dupes = sorted({f for f in frames if frames.count(f) > 1})
    if dupes:
        raise HTTPException(422, f"duplicate frames: {dupes}")
    bad = [f for f in frames if not 0 <= f < info["frame_count"]]
    if bad:
        raise HTTPException(422, f"frames out of range 0..{info['frame_count'] - 1}: {bad}")
    marks = sorted(body.marks, key=lambda m: m.frame)

    stem = path.stem
    label = {
        "video_id": stem,
        "fps": info["fps"],
        "contact_frames": [m.frame for m in marks],
        "stroke_labels": [m.stroke for m in marks],
        "racket_hand": body.racket_hand,
        "source": "real",
    }
    meta = {
        "source": "owner_labeling_page",
        "complete": body.complete,
        "frame_numbering": "opencv_decoder",
        "racket_hand": body.racket_hand,
        "reviewer": "project owner",
        "date": _dt.date.today().isoformat(),
        "saved_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "decoded_frame_count": info["frame_count"],
    }
    label_path, meta_path = _label_paths(stem)
    _backup(label_path, meta_path)
    _write_json(label_path, label)
    _write_json(meta_path, meta)
    return {"label": label, "meta": meta, "path": str(label_path)}


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _backup(*paths: Path) -> None:
    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S%f")
    for p in paths:
        if p.exists():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, BACKUP_DIR / f"{p.name}.{stamp}")


def main(argv: Optional[list[str]] = None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(prog="python -m labeling.label_server")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    print(f"影片資料夾: {library.video_dir()}")
    print(f"標記存放處: {labels_dir()}")
    print(f"請用 Chrome 或 Edge 開啟 http://localhost:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
