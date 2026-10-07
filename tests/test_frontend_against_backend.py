"""Drives *real* requests through the real FastAPI upload endpoint
(same pattern as tests/test_upload_api.py) and feeds each real response
into the *actual* frontend classification logic (frontend/upload_flow.js,
via frontend/classify_cli.js) over a subprocess bridge to Node -- not a
reimplementation of that logic in Python. This is the property
design/upload-flow.md cares about: for each of the 3 real API response
shapes (pass, fps FAIL, malformed file), the frontend picks the screen
the design doc specifies.

Skips gracefully if Node isn't available in this environment.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend import db  # noqa: E402
from backend.api.upload import app  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "tests"))
from synth import write_steady_textured_video  # noqa: E402

CLASSIFY_CLI = REPO_ROOT / "frontend" / "classify_cli.js"

NODE = shutil.which("node")


def _skip_if_no_node():
    if NODE is None:
        pytest.skip("Node not available in this environment")


def classify_via_frontend(http_status: int, body: dict) -> dict:
    """Runs the real frontend/upload_flow.js classifyUploadResponse()
    against a real (http_status, body) pair from the FastAPI test client,
    via the Node bridge script. Returns the parsed {screen, messages, ...}
    dict exactly as the frontend would compute it.
    """
    proc = subprocess.run(
        [NODE, str(CLASSIFY_CLI)],
        input=json.dumps({"httpStatus": http_status, "body": body}),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, f"classify_cli.js failed: {proc.stderr}"
    return json.loads(proc.stdout)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.api.upload.UPLOAD_DIR", tmp_path / "uploads")
    return TestClient(app)


def _skip_if_no_encoder(path):
    if not Path(path).exists() or Path(path).stat().st_size == 0:
        pytest.skip("此環境缺少 mp4 編碼器")


def test_real_passing_response_routes_to_info_confirm(tmp_path, client):
    """A real 60fps, steady, textured video -> passed:true with a
    non-empty messages_zh (court_corners is always not_implemented today)
    -> design doc §3a's info-card screen, not straight to §3a's plain
    "submitted" screen (that's reserved for the day court_corners is real
    and also passes).
    """
    _skip_if_no_node()
    video = tmp_path / "good.mp4"
    try:
        write_steady_textured_video(video, fps=60, n_frames=90)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    _skip_if_no_encoder(video)

    with open(video, "rb") as f:
        resp = client.post("/upload", files={"file": ("good.mp4", f, "video/mp4")})
    assert resp.status_code == 200

    result = classify_via_frontend(resp.status_code, resp.json())
    assert result["screen"] == "info_confirm"
    assert result["messages"] == resp.json()["messages_zh"]
    assert result["uploadId"] == resp.json()["upload_id"]


def test_real_fps_fail_response_routes_to_reshoot(tmp_path, client):
    """A real low-fps video -> passed:false -> design doc §3b's blocking
    re-shoot screen, listing messages_zh verbatim.
    """
    _skip_if_no_node()
    video = tmp_path / "low_fps.mp4"
    try:
        write_steady_textured_video(video, fps=30, n_frames=45)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    _skip_if_no_encoder(video)

    with open(video, "rb") as f:
        resp = client.post(
            "/upload", files={"file": ("low_fps.mp4", f, "video/mp4")}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["passed"] is False

    result = classify_via_frontend(resp.status_code, body)
    assert result["screen"] == "reshoot"
    assert result["messages"] == body["messages_zh"]
    assert result["uploadId"] is None


def test_real_malformed_file_response_routes_to_error_not_reshoot(tmp_path, client):
    """A real malformed/non-video file -> HTTP 422 {"error":
    "invalid_video_file", "detail": <english>} -> design doc §4's
    dedicated error screen (distinct visual language from §3b), with our
    invented Traditional Chinese copy -- never the raw English `detail`,
    and never routed to the re-shoot screen.
    """
    _skip_if_no_node()
    bogus = tmp_path / "not_a_video.mp4"
    bogus.write_bytes(b"this is definitely not a video container")

    with open(bogus, "rb") as f:
        resp = client.post(
            "/upload", files={"file": ("not_a_video.mp4", f, "video/mp4")}
        )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "invalid_video_file"

    result = classify_via_frontend(resp.status_code, body)
    assert result["screen"] == "error"
    assert result["errorCode"] == "invalid_video_file"
    # Must be our Traditional Chinese mapping, not the backend's raw
    # English `detail` string.
    assert result["messages"][0] != body["detail"]
    assert "檔案格式不支援" in result["messages"][0]


@pytest.mark.parametrize("suffix", ["", "/shots"])
def test_real_unknown_upload_id_routes_to_error_with_not_found_copy(
    tmp_path, monkeypatch, suffix
):
    """Real GET /uploads/<nonexistent> (and /shots) -> 404
    {"error": "upload_not_found"} -> error screen with the §4.1 copy.
    Not reachable from today's upload page (it only POSTs); this proves
    the mapping works for the real envelope for future dashboard callers.
    """
    _skip_if_no_node()
    monkeypatch.setenv(db.DB_ENV_VAR, str(tmp_path / "test.sqlite3"))
    resp = TestClient(app).get(f"/uploads/does-not-exist{suffix}")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"] == "upload_not_found"

    result = classify_via_frontend(resp.status_code, body)
    assert result["screen"] == "error"
    assert result["errorCode"] == "upload_not_found"
    assert result["messages"][0] != body["detail"]
    assert "找不到這筆上傳紀錄" in result["messages"][0]
    assert "與影片品質無關" in result["messages"][0]


# Runs the real frontend buildUploadFormData() and prints its non-file
# fields, so the test below posts exactly what upload.js would send.
_FORM_FIELDS_JS = """
const { buildUploadFormData } = require(process.argv[1]);
const fd = buildUploadFormData(new Blob(["x"]), JSON.parse(process.argv[2]));
const out = {};
for (const [k, v] of fd.entries()) if (k !== "file") out[k] = v;
process.stdout.write(JSON.stringify(out));
"""


def frontend_form_fields(racket_hand_choice: str) -> dict:
    proc = subprocess.run(
        [NODE, "-e", _FORM_FIELDS_JS, str(REPO_ROOT / "frontend" / "upload_flow.js"),
         json.dumps(racket_hand_choice)],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, f"buildUploadFormData failed: {proc.stderr}"
    return json.loads(proc.stdout)


@pytest.mark.parametrize("choice,stored", [("right", "right"), ("left", "left"), ("", None)])
def test_real_endpoint_accepts_the_racket_hand_the_frontend_sends(
    tmp_path, client, monkeypatch, choice, stored
):
    """Each §1.1 picker value (右手 "right", 左手 "left", 不確定 "") goes
    through the real buildUploadFormData, then the real POST /upload: never
    a 422 invalid_racket_hand, and the stored racket_hand matches (不確定 ->
    null, i.e. not given).
    """
    _skip_if_no_node()
    monkeypatch.setenv(db.DB_ENV_VAR, str(tmp_path / "test.sqlite3"))
    fields = frontend_form_fields(choice)
    assert fields == ({} if stored is None else {"racket_hand": stored})

    video = tmp_path / "good.mp4"
    try:
        write_steady_textured_video(video, fps=60, n_frames=90)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    _skip_if_no_encoder(video)

    with open(video, "rb") as f:
        resp = client.post("/upload", files={"file": ("good.mp4", f, "video/mp4")},
                           data=fields)
    assert resp.status_code == 200, resp.json()
    assert classify_via_frontend(resp.status_code, resp.json())["screen"] == "info_confirm"
    assert db.get_upload(resp.json()["upload_id"])["racket_hand"] == stored
