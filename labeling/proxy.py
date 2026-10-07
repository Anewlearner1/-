"""Labeling proxy: a browser-playable copy of a clip with the OpenCV decoder
frame number burned into every frame.

Why: a media player (or a browser seeking by time) can number frames several
frames differently from ``cv2.VideoCapture`` -- see the "frame numbering"
sections of ``docs/real-footage-findings.md``. Every label must be in OpenCV
decoder frame numbers, so the labeling page never trusts ``currentTime``: it
reads the frame number back off the pixels it is showing.

Each proxy frame is the source frame (downscaled so the longer side is at most
960 px) with a ``BAR_H``-pixel barcode strip added *above* it (no picture is
covered) and a large "decoder frame N" caption burned into the picture.

Barcode layout (``N_CELLS`` = 28 equal-width cells across the full width,
pure black/white, full strip height)::

    cell  0      white reference
    cell  1      black reference
    cells 2..21  20 data bits, frame number, most significant bit first
    cells 22..25 4 check bits = XOR of the five 4-bit nibbles of the number
    cell  26     white reference
    cell  27     black reference

White = 1, black = 0. A reader averages the middle of each cell, thresholds
halfway between the mean of the white and black references, and rejects the
read if the references lack contrast or the check bits disagree. The same
layout is implemented in ``labeling/label_logic.js``; keep the two in sync.

Proxies are cached under ``data/label_proxies/`` (gitignored, like all of
``data/``), keyed by file name, size, mtime and ``PROXY_VERSION``.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

PROXY_VERSION = 1
MAX_SIDE = 960
MIN_WIDTH = 224           # keeps every barcode cell at least 8 px wide
BAR_H = 32                # barcode strip height in px (>= 24 required)
N_DATA_BITS = 20
N_CHECK_BITS = 4
N_CELLS = 2 + N_DATA_BITS + N_CHECK_BITS + 2
MAX_FRAMES = 1 << N_DATA_BITS

PROXY_DIR_ENV = "RALLY_LABEL_PROXY_DIR"
DEFAULT_PROXY_DIR = Path(__file__).resolve().parent.parent / "data" / "label_proxies"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def proxy_dir() -> Path:
    path = Path(os.environ.get(PROXY_DIR_ENV) or DEFAULT_PROXY_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def check_bits(value: int) -> int:
    x = 0
    for shift in range(0, N_DATA_BITS, 4):
        x ^= (value >> shift) & 0xF
    return x


def barcode_cells(frame_index: int) -> list[int]:
    """1/0 per cell, left to right (1 = white)."""
    if not 0 <= frame_index < MAX_FRAMES:
        raise ValueError(f"frame index {frame_index} does not fit in {N_DATA_BITS} bits")
    data = [(frame_index >> (N_DATA_BITS - 1 - i)) & 1 for i in range(N_DATA_BITS)]
    chk = check_bits(frame_index)
    check = [(chk >> (N_CHECK_BITS - 1 - i)) & 1 for i in range(N_CHECK_BITS)]
    return [1, 0] + data + check + [1, 0]


def _cell_edges(width: int) -> list[int]:
    return [round(i * width / N_CELLS) for i in range(N_CELLS + 1)]


def draw_barcode(strip: np.ndarray, frame_index: int) -> None:
    """Paint the barcode into ``strip`` (BAR_H x W x 3, modified in place)."""
    edges = _cell_edges(strip.shape[1])
    for i, bit in enumerate(barcode_cells(frame_index)):
        strip[:, edges[i]:edges[i + 1]] = 255 if bit else 0


def read_barcode(frame: np.ndarray) -> Optional[int]:
    """Frame number from a decoded proxy frame (BGR or gray), or None."""
    strip = frame[:BAR_H]
    if strip.ndim == 3:
        strip = strip.mean(axis=2)
    w = strip.shape[1]
    y0, y1 = BAR_H // 4, BAR_H - BAR_H // 4
    means = []
    for i in range(N_CELLS):
        a, b = i * w / N_CELLS, (i + 1) * w / N_CELLS
        x0, x1 = int(a + (b - a) * 0.25), int(np.ceil(b - (b - a) * 0.25))
        means.append(float(strip[y0:y1, x0:max(x1, x0 + 1)].mean()))
    white = (means[0] + means[-2]) / 2
    black = (means[1] + means[-1]) / 2
    if white - black < 60:
        return None
    thr = (white + black) / 2
    bits = [1 if m > thr else 0 for m in means]
    if bits[:2] != [1, 0] or bits[-2:] != [1, 0]:
        return None
    value = 0
    for b in bits[2:2 + N_DATA_BITS]:
        value = (value << 1) | b
    chk = 0
    for b in bits[2 + N_DATA_BITS:2 + N_DATA_BITS + N_CHECK_BITS]:
        chk = (chk << 1) | b
    return value if chk == check_bits(value) else None


def _scaled_size(w: int, h: int) -> tuple[int, int]:
    scale = min(1.0, MAX_SIDE / max(w, h))
    if w * scale < MIN_WIDTH:
        scale = MIN_WIDTH / w
    sw = max(2, int(round(w * scale / 2)) * 2)
    sh = max(2, int(round(h * scale / 2)) * 2)
    return sw, sh


def _draw_caption(img: np.ndarray, frame_index: int) -> None:
    text = f"decoder frame {frame_index}"
    h, w = img.shape[:2]
    scale = max(0.6, min(w, h) / 360.0)
    thick = max(2, int(round(scale * 2)))
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    pad = int(6 * scale)
    x0, y1 = pad, h - pad
    cv2.rectangle(img, (x0 - pad // 2, y1 - th - base - pad), (x0 + tw + pad // 2, y1), (0, 0, 0), -1)
    cv2.putText(img, text, (x0, y1 - base - pad // 2), cv2.FONT_HERSHEY_SIMPLEX, scale,
                (0, 255, 255), thick, cv2.LINE_AA)


def make_label_proxy(video_path: "str | Path", out_path: "str | Path") -> dict:
    """Write the labeling proxy for ``video_path`` to ``out_path`` (VP8 WebM).

    Returns ``{fps, frame_count, width, height}`` of the proxy, where
    ``frame_count`` is the number of frames ``cv2.VideoCapture`` actually
    decoded (not the container's claimed count) and width/height include the
    barcode strip. The written file is decoded back and every frame's barcode
    checked against its index; a mismatch raises RuntimeError.
    """
    video_path, out_path = Path(video_path), Path(out_path)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"could not open video file: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if fps <= 0:
        cap.release()
        raise ValueError(f"video reports no fps: {video_path}")
    tmp = out_path.with_name(out_path.stem + ".partial.webm")
    writer = None
    n = 0
    size = None
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if size is None:
                sw, sh = _scaled_size(frame.shape[1], frame.shape[0])
                size = (sw, sh + BAR_H)
                writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"VP80"), fps, size)
                if not writer.isOpened():
                    raise RuntimeError("VP8 WebM encoder unavailable")
            if n >= MAX_FRAMES:
                raise ValueError(f"video has more than {MAX_FRAMES} frames")
            out = np.empty((size[1], size[0], 3), np.uint8)
            pic = cv2.resize(frame, (size[0], size[1] - BAR_H), interpolation=cv2.INTER_AREA)
            _draw_caption(pic, n)
            out[BAR_H:] = pic
            draw_barcode(out[:BAR_H], n)
            writer.write(out)
            n += 1
    finally:
        cap.release()
        if writer is not None:
            writer.release()
    if n == 0:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"no frames could be decoded from {video_path}")
    if not tmp.exists() or tmp.stat().st_size == 0:
        raise RuntimeError(f"VP8 writer produced no output for {video_path}")
    bad = verify_proxy(tmp, n)
    if bad:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"proxy barcode check failed for {video_path}: {bad[:5]}")
    os.replace(tmp, out_path)
    return {"fps": fps, "frame_count": n, "width": size[0], "height": size[1]}


def verify_proxy(path: "str | Path", expected_frames: int) -> list:
    """Decode a proxy and list problems: (index, barcode read) mismatches or a count error."""
    cap = cv2.VideoCapture(str(path))
    bad, i = [], 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            got = read_barcode(frame)
            if got != i:
                bad.append((i, got))
            i += 1
    finally:
        cap.release()
    if i != expected_frames:
        bad.append(("frame_count", i, expected_frames))
    return bad


def _cache_key(video_path: Path) -> str:
    st = video_path.stat()
    raw = f"{video_path.name}|{st.st_size}|{st.st_mtime_ns}|v{PROXY_VERSION}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def cached_proxy(video_path: "str | Path") -> tuple[Path, dict]:
    """Build (or reuse) the cached proxy; returns ``(webm_path, info)``."""
    video_path = Path(video_path)
    base = proxy_dir() / f"{video_path.stem}-{_cache_key(video_path)}"
    # String concatenation, not with_suffix: stems may contain dots.
    webm, info_path = Path(f"{base}.webm"), Path(f"{base}.json")
    with _locks_guard:
        lock = _locks.setdefault(str(base), threading.Lock())
    with lock:
        if webm.exists() and info_path.exists():
            return webm, json.loads(info_path.read_text(encoding="utf-8"))
        info = make_label_proxy(video_path, webm)
        info_path.write_text(json.dumps(info), encoding="utf-8")
        return webm, info
