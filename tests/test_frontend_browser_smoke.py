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
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert errors == []
    rows = sqlite3.connect(tmp_path / "b.sqlite3").execute(
        "SELECT status, racket_hand FROM uploads").fetchall()
    assert rows == [("queued", "left")]
