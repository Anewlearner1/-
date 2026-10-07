"""Real-browser test of the labeling page (headless Chromium via Playwright).

Skipped unless the `playwright` package and a Chromium binary exist (the cloud
sandbox has Chromium at /opt/pw-browsers). Serves labeling/label_server.py on
a local port, opens a synthetic clip's proxy, steps with the arrow keys and
seeks to several frames (0, 1, middle, last, ...) and asserts the decoder
frame the page displays equals the requested frame. Independently of the
page's own reader, a screenshot of the <video> element is decoded with
labeling/proxy.read_barcode to confirm the pixels on screen are that frame.
Then marks F and B, saves, and checks the JSON on disk.
"""
import glob
import json
import threading
import time

import cv2
import numpy as np
import pytest

sync_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

CHROME = (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome") or [None])[0]


def _write_clip(path, fps, n, width=480, height=270):
    wr = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    base = np.zeros((height, width, 3), np.uint8)
    base[..., 1] = np.linspace(60, 160, width, dtype=np.uint8)[None, :]
    for i in range(n):
        img = base.copy()
        cv2.circle(img, ((i * 13) % width, height // 2), 12, (40, 230, 230), -1)
        wr.write(img)
    wr.release()
    return path


def _serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    return server, thread


def _screen_frame(page):
    """Decoder frame read from a screenshot of the visible <video> element."""
    from labeling import proxy
    png = page.locator("#video").screenshot()
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    vw, vh = page.evaluate("[video.videoWidth, video.videoHeight]")
    return proxy.read_barcode(cv2.resize(img, (vw, vh), interpolation=cv2.INTER_AREA))


@pytest.mark.skipif(CHROME is None, reason="no Chromium binary")
@pytest.mark.parametrize("fps,n,port", [(30.0, 75, 8771), (29.97, 64, 8772)])
def test_page_shows_decoder_frames_and_saves_them(tmp_path, monkeypatch, fps, n, port):
    from labeling import label_server

    vids = tmp_path / "videos"
    vids.mkdir()
    monkeypatch.setenv("RALLY_VIDEO_DIR", str(vids))
    monkeypatch.setenv("RALLY_LABEL_DIR", str(tmp_path / "labels"))
    monkeypatch.setenv("RALLY_LABEL_PROXY_DIR", str(tmp_path / "proxies"))
    monkeypatch.setattr(label_server, "BACKUP_DIR", tmp_path / "backups")
    _write_clip(vids / "rally.mp4", fps, n)
    mid, last = n // 2, n - 1

    server, thread = _serve(label_server.app, port)
    results = []                                   # (how, requested, page shows, screenshot)
    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("dialog", lambda d: d.accept())
            page.goto(f"http://127.0.0.1:{port}/")
            page.click("#video-list button[data-name='rally.mp4']")
            page.wait_for_function("window.video = document.getElementById('video'); "
                                   "labelPage.state.curFrame === 0", timeout=60000)

            def check(how, requested):
                page.evaluate("labelPage.idle()")
                shown = page.text_content("#frame-num")
                results.append((how, requested, shown, _screen_frame(page)))

            check("open", 0)
            page.keyboard.press("ArrowRight"); check("→", 1)
            page.keyboard.press("ArrowRight"); check("→", 2)
            page.keyboard.press("ArrowLeft"); check("←", 1)
            page.keyboard.press("ArrowLeft"); check("←", 0)
            page.keyboard.press("ArrowLeft"); check("← at start", 0)
            page.keyboard.press("Shift+ArrowRight"); check("Shift+→", 10)
            page.keyboard.press("Shift+ArrowLeft"); check("Shift+←", 0)
            for f in (mid, last, 1, 0, mid + 1, 7):
                page.fill("#goto", str(f)); page.click("#btn-goto"); check("goto", f)
            page.keyboard.press("ArrowRight"); check("→", 8)
            page.evaluate(f"labelPage.seekToFrame({last - 1})"); check("seek", last - 1)
            page.keyboard.press("ArrowRight"); check("→", last)
            page.keyboard.press("ArrowRight"); check("→ at end", last)
            for _ in range(5):                       # five fast presses, queued
                page.keyboard.press("ArrowLeft")
            check("←×5", last - 5)

            natural_corrections = page.evaluate("labelPage.state.corrections")

            # Simulate a browser whose time->frame mapping is off by 3 frames
            # (e.g. an MP4 edit list): the barcode check must still land exactly.
            page.evaluate(f"labelPage.state.testTimeOffset = 3 / {fps}")
            for f in (20, 0, last):
                page.fill("#goto", str(f)); page.click("#btn-goto"); check("goto, +3 offset", f)
            page.keyboard.press("ArrowLeft"); check("←, +3 offset", last - 1)
            page.evaluate(f"labelPage.state.testTimeOffset = -4 / {fps}")
            page.fill("#goto", "30"); page.click("#btn-goto"); check("goto, -4 offset", 30)
            page.keyboard.press("ArrowRight"); check("→, -4 offset", 31)
            forced_corrections = page.evaluate("labelPage.state.corrections") - natural_corrections
            page.evaluate("labelPage.state.testTimeOffset = 0")

            # Play a little, pause, and the shown frame must match the screen.
            page.fill("#goto", "5"); page.click("#btn-goto"); check("goto", 5)
            page.keyboard.press("Space")
            page.wait_for_function("!video.paused", timeout=5000)
            page.wait_for_timeout(600)
            page.keyboard.press("Space")
            page.wait_for_function("video.paused", timeout=5000)
            page.wait_for_timeout(400)
            page.evaluate("labelPage.idle()")
            shown = page.text_content("#frame-num")
            results.append(("after play/pause", "-", shown, _screen_frame(page)))

            # Mark F at 12 and B at mid via keys, then save.
            page.fill("#goto", "12"); page.click("#btn-goto"); check("goto", 12)
            page.keyboard.press("f")
            page.fill("#goto", str(mid)); page.click("#btn-goto"); check("goto", mid)
            page.keyboard.press("b")
            page.keyboard.press("ArrowRight"); check("→", mid + 1)
            page.keyboard.press("o")                 # extra mark, then remove it
            page.keyboard.press("Delete")
            page.evaluate("labelPage.idle()")
            marks = page.eval_on_selector_all("#marks button", "bs => bs.map(b => b.dataset.frame + b.dataset.stroke)")
            page.click("#marks button[data-frame='12']"); check("click mark", 12)
            page.get_by_label("右手").check()
            page.get_by_label("我已從頭看到尾，所有擊球都標了").check()
            page.click("#save")
            page.wait_for_function("document.getElementById('save-status').textContent.includes('已儲存')",
                                   timeout=10000)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    print(f"\nfps={fps} frames={n}: corrections without offset={natural_corrections}, "
          f"with injected offset={forced_corrections}")
    print("(how, requested, page shows, screenshot barcode)")
    for r in results:
        print("  ", r)
    assert errors == []
    for how, requested, shown, screen in results:
        if requested == "-":
            assert shown.isdigit() and int(shown) == screen, (how, shown, screen)
            assert 5 < screen < last, "pause should land mid-clip"
        else:
            assert shown == str(requested), (how, requested, shown)
            assert screen == requested, (how, requested, screen)
    assert forced_corrections >= 6        # the offset seeks really were corrected
    assert marks == ["12forehand", f"{mid}backhand"]

    label = json.loads((tmp_path / "labels" / "rally.json").read_text())
    assert label["contact_frames"] == [12, mid]
    assert label["stroke_labels"] == ["forehand", "backhand"]
    assert label["racket_hand"] == "right" and label["source"] == "real"
    assert label["fps"] == pytest.approx(fps, rel=1e-3)
    meta = json.loads((tmp_path / "labels" / "rally.meta.json").read_text())
    assert meta["complete"] is True and meta["frame_numbering"] == "opencv_decoder"


@pytest.mark.skipif(CHROME is None, reason="no Chromium binary")
def test_frames_fallback_mode_without_vp8(tmp_path, monkeypatch):
    """RALLY_LABEL_FORCE_FRAMES=1 simulates a PC whose OpenCV has no VP8 encoder:
    the page must switch to one JPEG per decoder frame and stay exact."""
    from labeling import label_server, proxy

    vids = tmp_path / "videos"
    vids.mkdir()
    monkeypatch.setenv("RALLY_VIDEO_DIR", str(vids))
    monkeypatch.setenv("RALLY_LABEL_DIR", str(tmp_path / "labels"))
    monkeypatch.setenv("RALLY_LABEL_PROXY_DIR", str(tmp_path / "proxies"))
    monkeypatch.setenv(proxy.FORCE_FRAMES_ENV, "1")
    monkeypatch.setattr(label_server, "BACKUP_DIR", tmp_path / "backups")
    n = 60
    _write_clip(vids / "rally.mp4", 30.0, n)
    server, thread = _serve(label_server.app, 8773)
    seen = []
    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("dialog", lambda d: d.accept())
            page.goto("http://127.0.0.1:8773/")
            page.click("#video-list button[data-name='rally.mp4']")
            page.wait_for_function("labelPage.state.curFrame === 0", timeout=60000)
            assert page.evaluate("labelPage.state.mode") == "frames"
            assert page.is_visible("#frame-img") and not page.is_visible("#video")

            def shown():
                page.evaluate("labelPage.idle()")
                png = page.locator("#frame-img").screenshot()
                img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
                w, h = page.evaluate("[document.getElementById('frame-img').naturalWidth,"
                                     " document.getElementById('frame-img').naturalHeight]")
                on_screen = proxy.read_barcode(cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA))
                return int(page.text_content("#frame-num")), on_screen

            page.keyboard.press("ArrowRight"); seen.append((1, *shown()))
            page.keyboard.press("Shift+ArrowRight"); seen.append((11, *shown()))
            for f in (n - 1, 0, 37):
                page.fill("#goto", str(f)); page.click("#btn-goto"); seen.append((f, *shown()))
            page.keyboard.press("ArrowLeft"); seen.append((36, *shown()))
            page.keyboard.press("Space"); page.wait_for_timeout(500); page.keyboard.press("Space")
            page.evaluate("labelPage.idle()")
            after, screen = shown()
            assert after > 36 and after == screen                 # playback advanced, display agrees
            page.fill("#goto", "20"); page.click("#btn-goto"); page.evaluate("labelPage.idle()")
            page.keyboard.press("b")
            page.check("input[name=hand][value=left]")
            page.check("#complete")
            page.click("#save")
            page.wait_for_function("document.getElementById('save-status').textContent.includes('已儲存')",
                                   timeout=10000)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert errors == []
    assert all(req == page_n == scr for req, page_n, scr in seen), seen
    lab = json.loads((tmp_path / "labels" / "rally.json").read_text())
    assert lab["contact_frames"] == [20] and lab["stroke_labels"] == ["backhand"]
    assert lab["racket_hand"] == "left"
