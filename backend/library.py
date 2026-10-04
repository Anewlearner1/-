"""Server-side video folder: where validation footage lives and how it is read.

One directory (default ``data/videos/``, override with ``RALLY_VIDEO_DIR``)
holds the source videos the pipeline reads directly -- no HTTP upload needed.
``data/`` is gitignored on purpose: footage contains identifiable people and
this repo is AGPL / public, so videos must never be committed.

This is a *convenience reader*, not storage you can rely on: the folder lives
on whatever machine runs the backend. In the ephemeral sandbox it disappears
when the container is recycled, so keep the original files elsewhere.

CLI::

    python -m backend.library ingest PATH [PATH ...]   # copy files in
    python -m backend.library scan [--shots]           # probe + quality gate
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

import cv2

VIDEO_DIR_ENV = "RALLY_VIDEO_DIR"
DEFAULT_VIDEO_DIR = Path(__file__).resolve().parent.parent / "data" / "videos"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def video_dir() -> Path:
    """The configured video folder, created if missing."""
    path = Path(os.environ.get(VIDEO_DIR_ENV) or DEFAULT_VIDEO_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_videos(directory: Optional[Path] = None) -> list[Path]:
    """Video files directly inside the folder, sorted by name (no recursion)."""
    directory = directory or video_dir()
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )


def resolve_video(name: str, directory: Optional[Path] = None) -> Path:
    """Map a bare file name to a path inside the folder.

    Raises ValueError for anything that is not a plain file name (path
    separators, ``..``) and FileNotFoundError if it is not in the folder, so
    callers that accept a name from a request cannot be walked out of it.
    """
    directory = (directory or video_dir()).resolve()
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError(f"not a plain file name: {name!r}")
    path = (directory / name).resolve()
    if path.parent != directory:
        raise ValueError(f"not a plain file name: {name!r}")
    if not path.is_file():
        raise FileNotFoundError(f"no such video in {directory}: {name}")
    return path


def safe_name(name: str) -> str:
    """File name reduced to ``[A-Za-z0-9._-]`` so it is shell/URL safe."""
    cleaned = _UNSAFE.sub("_", Path(name).name).strip("._") or "video"
    return cleaned


def ingest(source: Path, directory: Optional[Path] = None) -> Path:
    """Copy ``source`` into the folder; never overwrites an existing file.

    A name collision gets a numeric suffix, so ingesting twice keeps both
    copies rather than silently replacing footage someone may have labelled.
    """
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"source file not found: {source}")
    if source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError(
            f"unsupported extension {source.suffix!r}; expected one of "
            f"{sorted(VIDEO_EXTENSIONS)}")

    directory = directory or video_dir()
    stem_ext = Path(safe_name(source.name))
    dest = directory / stem_ext.name
    n = 1
    while dest.exists():
        dest = directory / f"{stem_ext.stem}_{n}{stem_ext.suffix}"
        n += 1
    shutil.copy2(source, dest)
    return dest


@dataclasses.dataclass
class VideoInfo:
    name: str
    size_mb: float
    fps: float
    frames: int
    width: int
    height: int
    duration_s: float

    @property
    def orientation(self) -> str:
        return "portrait" if self.height > self.width else "landscape"


def probe(path: Path) -> VideoInfo:
    """Container metadata via OpenCV. Raises ValueError if it cannot be opened."""
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError(f"could not open video file: {path}")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        cap.release()
    return VideoInfo(
        name=path.name,
        size_mb=round(path.stat().st_size / 1e6, 2),
        fps=round(fps, 2),
        frames=frames,
        width=width,
        height=height,
        duration_s=round(frames / fps, 2) if fps > 0 else 0.0,
    )


def scan_one(path: Path, *, shots: bool = False) -> dict:
    """Probe one video, run the upload quality gate, optionally detect shots.

    Never raises for a bad video: failure is reported in the returned dict so
    one corrupt file does not abort a whole-folder scan.
    """
    from backend.upload_quality import check_upload_quality

    result: dict = {"name": path.name}
    try:
        info = probe(path)
    except ValueError as exc:
        result["error"] = str(exc)
        return result
    result["info"] = dataclasses.asdict(info) | {"orientation": info.orientation}

    report = check_upload_quality(path)
    result["quality"] = {
        "passed": report.passed,
        "checks": {c.name: c.status.value for c in report.checks},
        "messages_zh": report.messages_zh,
    }

    if shots:
        # Imported lazily: pulls in MediaPipe and downloads a model on first use.
        from cv.pose_overlay import extract_player_landmarks
        from ml.shot_timing import detect_shots

        seq = extract_player_landmarks(path, progress=False)
        found = detect_shots(seq)
        result["shots"] = {
            "pose_detection_rate": round(seq.pose_detection_rate, 3),
            "shot_count": found.shot_count,
            "contact_frames": [e.contact_frame for e in found.events],
            "note": ("pose-speed peaks only, unvalidated: no labeled clips "
                     "exist and non-stroke arm motion gives false positives"),
        }
    return result


def scan(directory: Optional[Path] = None, *, shots: bool = False) -> list[dict]:
    return [scan_one(p, shots=shots) for p in list_videos(directory)]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.library")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_in = sub.add_parser("ingest", help="copy video files into the folder")
    p_in.add_argument("paths", nargs="+", type=Path)
    p_scan = sub.add_parser("scan", help="probe and quality-check every video")
    p_scan.add_argument("--shots", action="store_true",
                        help="also run pose + shot detection (slow)")
    args = parser.parse_args(argv)

    if args.cmd == "ingest":
        status = 0
        for src in args.paths:
            try:
                print(f"{src} -> {ingest(src)}")
            except (FileNotFoundError, ValueError) as exc:
                print(f"skip {src}: {exc}", file=sys.stderr)
                status = 1
        return status

    print(json.dumps({"video_dir": str(video_dir()),
                      "videos": scan(shots=args.shots)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
