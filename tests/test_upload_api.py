import sys
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from backend.api.upload import app  # noqa: E402
from backend.upload_quality import CheckStatus  # noqa: E402

from synth import (  # noqa: E402
    write_steady_textured_video,
    write_static_video,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Redirect the upload dir to a tmp_path so tests don't litter the repo
    # and stay isolated from each other.
    monkeypatch.setattr("backend.api.upload.UPLOAD_DIR", tmp_path / "uploads")
    return TestClient(app)


def _skip_if_no_encoder(path):
    if not Path(path).exists() or Path(path).stat().st_size == 0:
        pytest.skip("此環境缺少 mp4 編碼器")


def test_passing_upload_returns_passed_true_and_upload_id(tmp_path, client):
    video = tmp_path / "good.mp4"
    try:
        write_steady_textured_video(video, fps=60, n_frames=90)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    _skip_if_no_encoder(video)

    with open(video, "rb") as f:
        resp = client.post("/upload", files={"file": ("good.mp4", f, "video/mp4")})

    assert resp.status_code == 200
    body = resp.json()
    assert body["passed"] is True
    assert isinstance(body["checks"], list) and len(body["checks"]) == 3
    names = [c["name"] for c in body["checks"]]
    assert names == ["fps", "camera_stability", "court_corners"]
    # court_corners is always not_implemented today, and its message_zh
    # must still surface in messages_zh per design doc §0/§3a.
    corners = body["checks"][2]
    assert corners["status"] == CheckStatus.NOT_IMPLEMENTED.value
    assert corners["message_zh"] is not None
    assert body["messages_zh"] == [corners["message_zh"]]
    # Stub queue ack -- must exist, but this is explicitly not a real job id.
    assert "upload_id" in body
    assert isinstance(body["upload_id"], str) and body["upload_id"]


def test_failing_upload_low_fps_returns_passed_false_no_upload_id(tmp_path, client):
    video = tmp_path / "low_fps.mp4"
    try:
        write_steady_textured_video(video, fps=30, n_frames=45)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    _skip_if_no_encoder(video)

    with open(video, "rb") as f:
        resp = client.post("/upload", files={"file": ("low_fps.mp4", f, "video/mp4")})

    assert resp.status_code == 200
    body = resp.json()
    assert body["passed"] is False
    fps_check = body["checks"][0]
    assert fps_check["name"] == "fps"
    assert fps_check["status"] == CheckStatus.FAIL.value
    assert fps_check["message_zh"] is not None
    assert fps_check["message_zh"] in body["messages_zh"]
    # A failed gate must not get a queue ack.
    assert "upload_id" not in body


def test_malformed_file_returns_distinct_error_shape(tmp_path, client):
    bogus = tmp_path / "not_a_video.mp4"
    bogus.write_bytes(b"this is definitely not a video container")

    with open(bogus, "rb") as f:
        resp = client.post(
            "/upload", files={"file": ("not_a_video.mp4", f, "video/mp4")}
        )

    assert resp.status_code == 422
    body = resp.json()
    # Distinct shape from a QualityReport: no "passed"/"checks" keys, and
    # an "error" field instead, per design doc §4's ValueError-vs-FAIL note.
    assert "passed" not in body
    assert "checks" not in body
    assert body["error"] == "invalid_video_file"
    assert "detail" in body


def test_racket_hand_is_stored_and_an_invalid_one_is_rejected(tmp_path, client, monkeypatch):
    from backend import db
    monkeypatch.setenv(db.DB_ENV_VAR, str(tmp_path / "api.sqlite3"))
    video = tmp_path / "good.mp4"
    try:
        write_steady_textured_video(video, fps=60, n_frames=90)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    _skip_if_no_encoder(video)

    with open(video, "rb") as f:
        bad = client.post("/upload", files={"file": ("good.mp4", f, "video/mp4")},
                          data={"racket_hand": "both"})
    assert bad.status_code == 422 and bad.json()["error"] == "invalid_racket_hand"
    assert not (tmp_path / "uploads").exists()           # rejected before the file is stored

    with open(video, "rb") as f:
        ok = client.post("/upload", files={"file": ("good.mp4", f, "video/mp4")},
                         data={"racket_hand": "left"})
    assert ok.status_code == 200
    assert db.get_upload(ok.json()["upload_id"])["racket_hand"] == "left"

    with open(video, "rb") as f:
        none = client.post("/upload", files={"file": ("good.mp4", f, "video/mp4")})
    assert db.get_upload(none.json()["upload_id"])["racket_hand"] is None
