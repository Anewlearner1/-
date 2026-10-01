import pytest

pytest.importorskip("cv2")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from backend import db  # noqa: E402
from backend.api.upload import app  # noqa: E402
from ml.shot_timing import detect_shots  # noqa: E402
from synth import write_static_video, write_steady_textured_video  # noqa: E402
from tests.synth_pose import synth_landmark_sequence  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.api.upload.UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setenv(db.DB_ENV_VAR, str(tmp_path / "test.sqlite3"))
    return TestClient(app)


def _post(client, path):
    with open(path, "rb") as f:
        return client.post("/upload", files={"file": (path.name, f, "video/mp4")})


def _video(tmp_path, name, writer, **kw):
    p = tmp_path / name
    try:
        writer(p, **kw)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    if not p.exists() or p.stat().st_size == 0:
        pytest.skip("此環境缺少 mp4 編碼器")
    return p


def test_pass_creates_queued_row(tmp_path, client):
    resp = _post(client, _video(tmp_path, "good.mp4", write_steady_textured_video,
                                fps=60, n_frames=90))
    body = resp.json()
    assert body["passed"] is True
    row = db.get_upload(body["upload_id"])
    assert row["status"] == "queued"
    assert row["original_filename"] == "good.mp4"
    assert row["quality_report"]["passed"] is True
    got = client.get(f"/uploads/{body['upload_id']}")
    assert got.status_code == 200
    assert got.json()["status"] == "queued"
    assert got.json()["quality_report"]["checks"]


def test_failed_gate_creates_no_row(tmp_path, client):
    resp = _post(client, _video(tmp_path, "slow.mp4", write_static_video,
                                fps=30, n_frames=60))
    body = resp.json()
    assert body["passed"] is False
    assert "upload_id" not in body
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM uploads").fetchone()[0] == 0


def test_unknown_id_404_envelope(client):
    for path in ("/uploads/nope", "/uploads/nope/shots"):
        r = client.get(path)
        assert r.status_code == 404
        assert r.json()["error"] == "upload_not_found"


def test_shots_nulls_not_zeros(client):
    uid = db.create_upload("a.mp4", "/x/a.mp4", {"passed": True})
    assert client.get(f"/uploads/{uid}/shots").json()["shot_count"] == 0
    result = detect_shots(synth_landmark_sequence([40, 110], fps=30.0))
    db.insert_shots_from_result(uid, result)
    body = client.get(f"/uploads/{uid}/shots").json()
    assert body["shot_count"] == result.shot_count == 2
    for s in body["shots"]:
        assert s["fh_bh_label"] is None
        assert s["ball_speed_kmh"] is None
        assert s["peak_speed"] > 0


def test_insert_from_shot_timing_result_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv(db.DB_ENV_VAR, str(tmp_path / "rt.sqlite3"))
    uid = db.create_upload(None, "/x/b.mp4", {"passed": True})
    result = detect_shots(synth_landmark_sequence([40, 110, 180], fps=30.0))
    assert db.insert_shots_from_result(uid, result) == 3
    shots = db.list_shots(uid)
    assert [s["shot_index"] for s in shots] == [0, 1, 2]
    for s, e in zip(shots, result.events):
        assert s["contact_frame"] == e.contact_frame
        assert s["contact_time_s"] == pytest.approx(e.contact_time_s)
        assert s["peak_speed"] == pytest.approx(e.peak_speed)
        assert s["wrist"] == e.wrist
        assert s["source"] == "ml.shot_timing.detect_shots"
