"""Real FastAPI responses (GET /uploads/{id}, /shots, /video) fed into the
*actual* frontend/dashboard_logic.js via Node -- not a Python
reimplementation. Proves the dashboard consumes the real /shots shape:
fh_bh_status drives the pill, "other" is not counted, nothing labeled ->
尚未分析, ball speed never shows peak_speed.

Skips if Node is unavailable.
"""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from backend import db  # noqa: E402
from backend.api.upload import app  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGIC = REPO_ROOT / "frontend" / "dashboard_logic.js"
NODE = shutil.which("node")

_BRIDGE_JS = """
const L = require(process.argv[1]);
let raw = "";
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {
  const { status, upload, shots, times } = JSON.parse(raw);
  const view = L.buildShotsView(shots.httpStatus, shots.body);
  process.stdout.write(JSON.stringify({
    status: L.classifyUploadStatus(upload.httpStatus, upload.body),
    view,
    highlights: (times || []).map((t) => L.currentShotIndex(view.shots || [], t)),
  }));
});
"""


def run_logic(upload_resp, shots_resp, times=()):
    if NODE is None:
        pytest.skip("Node not available in this environment")
    payload = {
        "upload": {"httpStatus": upload_resp.status_code, "body": upload_resp.json()},
        "shots": {"httpStatus": shots_resp.status_code, "body": shots_resp.json()},
        "times": list(times),
    }
    proc = subprocess.run([NODE, "-e", _BRIDGE_JS, str(LOGIC)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(db.DB_ENV_VAR, str(tmp_path / "test.sqlite3"))
    return TestClient(app)


def _events(times, fps=60.0):
    return SimpleNamespace(events=[
        SimpleNamespace(contact_frame=int(round(t * fps)), contact_time_s=t,
                        peak_speed=1234.5, wrist="right")
        for t in times
    ])


def _upload(tmp_path, status="done", video_bytes=b""):
    video = tmp_path / "clip.mp4"
    video.write_bytes(video_bytes)
    uid = db.create_upload("clip.mp4", str(video), {"passed": True})
    if status != "queued":
        db.set_upload_status(uid, status, error="RuntimeError: boom" if status == "failed" else None)
    return uid


def _get(client, uid):
    return client.get(f"/uploads/{uid}"), client.get(f"/uploads/{uid}/shots")


@pytest.mark.parametrize("status", ["queued", "processing"])
def test_in_progress_upload_is_loading_and_polls(tmp_path, client, status):
    out = run_logic(*_get(client, _upload(tmp_path, status)))
    assert out["status"]["state"] == "loading"
    assert out["status"]["poll"] is True


def test_failed_upload_shows_chinese_error_not_raw_exception(tmp_path, client):
    out = run_logic(*_get(client, _upload(tmp_path, "failed")))
    assert out["status"]["state"] == "failed"
    assert "boom" not in out["status"]["messages"][0]
    assert out["status"]["technicalDetail"] == "RuntimeError: boom"


def test_done_with_zero_shots_is_empty_state(tmp_path, client):
    out = run_logic(*_get(client, _upload(tmp_path)))
    assert out["status"]["state"] == "ready"
    assert out["view"]["state"] == "empty"
    assert "沒有偵測到擊球" in out["view"]["messages"][0]


def test_unclassified_shots_highlight_and_placeholders(tmp_path, client):
    uid = _upload(tmp_path)
    db.insert_shots_from_result(uid, _events([1.0, 2.5, 4.0]))
    out = run_logic(*_get(client, uid), times=[0.5, 1.0, 2.49, 2.5, 10.0])
    view = out["view"]
    assert view["state"] == "normal"
    assert out["highlights"] == [-1, 0, 0, 1, 2]
    assert view["summary"]["shotCount"] == 3
    assert view["summary"]["forehand"]["text"] == "尚未分析"
    assert view["summary"]["backhand"]["text"] == "尚未分析"
    for card in view["cards"]:
        assert card["fhBh"]["text"] == "尚未分析"
        assert card["ballSpeed"]["text"] == "尚未分析"
    assert "1234" not in json.dumps(view["cards"])  # wrist peak_speed never shown


def test_classified_shots_real_shape(tmp_path, client):
    uid = _upload(tmp_path)
    db.insert_shots_from_result(
        uid, _events([1.0, 2.0, 3.0, 4.0, 5.0]),
        stroke_labels=["forehand", "backhand", "unknown", "other", "forehand"],
        stroke_confidences=[0.9, 0.3, None, 0.8, 0.6],
    )
    upload_resp, shots_resp = _get(client, uid)
    statuses = [s["fh_bh_status"] for s in shots_resp.json()["shots"]]
    assert statuses == ["labeled", "labeled", "undetermined", "labeled", "labeled"]
    view = run_logic(upload_resp, shots_resp)["view"]
    assert [c["fhBh"]["text"] for c in view["cards"]] == ["正手", "推測：反手", "無法判斷", "不適用", "正手"]
    summary = view["summary"]
    assert summary["forehand"]["count"] == 2
    assert summary["backhand"]["count"] == 1  # "other" not counted (ADR 0002)
    assert summary["experimental"] is True


def test_all_undetermined_summary_is_not_zero(tmp_path, client):
    uid = _upload(tmp_path)
    db.insert_shots_from_result(uid, _events([1.0, 2.0]), stroke_labels=["unknown", "unknown"],
                                stroke_confidences=[None, None])
    view = run_logic(*_get(client, uid))["view"]
    assert view["summary"]["forehand"]["text"] == "無法判斷"
    assert view["summary"]["forehand"]["count"] is None


def test_unknown_id_uses_upload_not_found_copy(client):
    out = run_logic(*_get(client, "does-not-exist"))
    for r in (out["status"], out["view"]):
        assert r["state"] == "error"
        assert r["errorCode"] == "upload_not_found"
        assert "找不到這筆上傳紀錄" in r["messages"][0]


def test_video_url_the_page_uses_supports_range(tmp_path, client):
    """dashboard.js plays /uploads/{id}/video; seeking needs a 206 to a Range request."""
    uid = _upload(tmp_path, video_bytes=bytes(range(256)) * 8)
    r = client.get(f"/uploads/{uid}/video", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.content == (bytes(range(256)) * 8)[10:20]
    assert r.headers["content-type"].startswith("video/mp4")
    assert "/video" in (REPO_ROOT / "frontend" / "dashboard.js").read_text(encoding="utf-8")
