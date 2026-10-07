"""Real-browser smoke test of the upload page (headless Chromium via Playwright).

Skipped unless the `playwright` Python package and a Chromium binary exist
(the cloud sandbox has Chromium at /opt/pw-browsers). It serves frontend/
from the real FastAPI app on a local port, picks a racket hand, uploads a
synthetic 60 fps clip and checks the stored row -- the one path the Node
tests cannot cover (real DOM, real fetch, real multipart)."""
import glob
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

CHROME = (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome") or [None])[0]


@pytest.mark.skipif(CHROME is None, reason="no Chromium binary")
def test_upload_page_sends_the_chosen_racket_hand(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    import backend.api.upload as up
    from backend import db
    from synth import write_steady_textured_video

    monkeypatch.setenv(db.DB_ENV_VAR, str(tmp_path / "b.sqlite3"))
    monkeypatch.setattr(up, "UPLOAD_DIR", tmp_path / "uploads")
    shell = FastAPI()
    shell.mount("/static", StaticFiles(directory=str(Path(__file__).parent.parent / "frontend")))
    shell.mount("/", up.app)
    server = uvicorn.Server(uvicorn.Config(shell, host="127.0.0.1", port=8766, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    video = write_steady_textured_video(tmp_path / "good.mp4", 60.0, 90)
    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME)
            page = browser.new_page(viewport={"width": 420, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto("http://127.0.0.1:8766/static/upload.html")
            page.get_by_label("左手").check()
            page.set_input_files("input[type=file]", str(video))
            page.wait_for_selector("#screen-info_confirm:not([hidden]), #screen-submitted:not([hidden])",
                                   timeout=15000)
            if page.is_visible("#screen-info_confirm"):
                page.click("#info-continue-btn")
            href = page.get_attribute("#submitted-dashboard-link", "href")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert errors == []
    rows = sqlite3.connect(tmp_path / "b.sqlite3").execute(
        "SELECT status, racket_hand FROM uploads").fetchall()
    assert rows == [("queued", "left")]
    uid = sqlite3.connect(tmp_path / "b.sqlite3").execute("SELECT id FROM uploads").fetchone()[0]
    assert href == f"dashboard.html?upload={uid}"


def _serve(tmp_path, monkeypatch, port):
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    import backend.api.upload as up
    from backend import db
    monkeypatch.setenv(db.DB_ENV_VAR, str(tmp_path / "d.sqlite3"))
    monkeypatch.setattr(up, "UPLOAD_DIR", tmp_path / "uploads")
    shell = FastAPI()
    shell.mount("/static", StaticFiles(directory=str(Path(__file__).parent.parent / "frontend")))
    shell.mount("/", up.app)
    server = uvicorn.Server(uvicorn.Config(shell, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    return server, thread


@pytest.mark.skipif(CHROME is None, reason="no Chromium binary")
def test_dashboard_highlight_follows_real_playback(tmp_path, monkeypatch):
    """VP8 WebM because the bundled open-source Chromium cannot decode H.264."""
    import cv2
    import numpy as np
    from backend import db
    from ml.shot_timing import ShotEvent

    video = tmp_path / "clip.webm"
    out = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"VP80"), 30.0, (160, 120))
    for i in range(90):                                    # 3 s
        out.write(np.full((120, 160, 3), (i * 3) % 255, np.uint8))
    out.release()
    if not video.exists() or video.stat().st_size == 0:
        pytest.skip("no VP8 encoder")
    server, thread = _serve(tmp_path, monkeypatch, 8769)
    try:
        uid = db.create_upload("clip.webm", str(video), {"passed": True}, status="done")
        result = type("R", (), {"events": [ShotEvent(15, 0.5, 1.0, "right"),
                                           ShotEvent(60, 2.0, 1.0, "right")]})()
        db.insert_shots_from_result(uid, result, stroke_labels=["forehand", "unknown"],
                                    stroke_confidences=[0.9, None])
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME,
                                        args=["--autoplay-policy=no-user-gesture-required"])
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:8769/static/dashboard.html?upload={uid}")
            page.wait_for_function("document.getElementById('video').readyState >= 1", timeout=15000)
            current = "(document.querySelector('#shot-list [aria-current]') || {}).textContent || ''"
            assert page.evaluate(current) == ""                      # before the first shot
            page.evaluate("v = document.getElementById('video'); v.muted = true; v.play()")
            page.wait_for_function("document.getElementById('video').currentTime > 1.0", timeout=10000)
            assert "第1拍" in page.evaluate(current)
            page.wait_for_function("document.getElementById('video').currentTime > 2.3", timeout=10000)
            assert "第2拍" in page.evaluate(current) and "無法判斷" in page.evaluate(current)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert errors == []
