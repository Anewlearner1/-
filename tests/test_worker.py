"""Tests for backend/worker.py. Pose extraction is replaced by a synthetic landmark
sequence (the worker's own queue/status/shot-storage logic is what is under test);
a real end-to-end run on a real video is recorded in docs/real-footage-findings.md."""
import pytest

from backend import db, worker
from tests.synth_pose import synth_landmark_sequence

FPS = 30.0


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv(db.DB_ENV_VAR, str(tmp_path / "w.sqlite3"))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"not decoded: pose extraction is stubbed")
    seq = synth_landmark_sequence([60, 140, 220], fps=FPS, n_frames=320)
    monkeypatch.setattr("cv.pose_overlay.extract_player_landmarks", lambda *a, **k: seq)
    return video


def _queue(video, name="a.mp4"):
    return db.create_upload(name, str(video), {"passed": True})


def test_a_queued_upload_becomes_done_with_its_shots_stored(env):
    uid = _queue(env)
    assert db.claim_next_queued()["upload_id"] == uid
    assert db.get_upload(uid)["status"] == "processing"
    assert worker.process_upload(uid) == 3
    assert db.get_upload(uid)["status"] == "done"
    shots = db.list_shots(uid)
    assert [s["contact_frame"] for s in shots] == pytest.approx([60, 140, 220], abs=2)
    assert all(s["fh_bh_label"] is None and s["ball_speed_kmh"] is None for s in shots)
    assert shots[0]["source"] == "ml.shot_timing.detect_shots"


def test_a_failure_marks_the_upload_failed_and_keeps_the_reason(tmp_path, monkeypatch):
    monkeypatch.setenv(db.DB_ENV_VAR, str(tmp_path / "f.sqlite3"))
    def boom(*a, **k):
        raise RuntimeError("could not open video")
    monkeypatch.setattr("cv.pose_overlay.extract_player_landmarks", boom)
    uid = db.create_upload("x.mp4", str(tmp_path / "missing.mp4"), {})
    assert worker.process_upload(uid) == 0
    row = db.get_upload(uid)
    assert row["status"] == "failed" and "could not open video" in row["error"]


def test_claim_takes_the_oldest_once_and_then_returns_none(env):
    first, second = _queue(env, "1.mp4"), _queue(env, "2.mp4")
    assert db.claim_next_queued()["upload_id"] == first
    assert db.claim_next_queued()["upload_id"] == second
    assert db.claim_next_queued() is None


def test_run_once_drains_the_queue_and_one_failure_does_not_stop_it(env, tmp_path, monkeypatch):
    good = _queue(env, "good.mp4")
    calls = {"n": 0}
    real = worker.process_upload
    def flaky(upload_id, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            db.set_upload_status(upload_id, "failed", error="first one fails")
            return 0
        return real(upload_id, **kw)
    monkeypatch.setattr(worker, "process_upload", flaky)
    second = _queue(env, "second.mp4")
    assert worker.run_once() == 2
    assert db.get_upload(good)["status"] == "failed"
    assert db.get_upload(second)["status"] == "done"


def test_merge_option_is_applied_and_recorded_in_the_source(env, monkeypatch):
    seq = synth_landmark_sequence([60, 72], fps=FPS, n_frames=160)
    monkeypatch.setattr("cv.pose_overlay.extract_player_landmarks", lambda *a, **k: seq)
    uid = _queue(env)
    db.claim_next_queued()
    assert worker.process_upload(uid, merge_within_s=0.5) == 1
    assert "merge_within_s=0.5" in db.list_shots(uid)[0]["source"]


def test_requeue_processing_recovers_a_crashed_worker(env):
    uid = _queue(env)
    db.claim_next_queued()
    assert db.requeue_processing() == 1
    assert db.get_upload(uid)["status"] == "queued"


def test_cli_once_reports_the_count(env, capsys):
    _queue(env)
    assert worker.main(["--once"]) == 0
    assert "processed 1 upload" in capsys.readouterr().out


def test_an_old_database_without_the_error_column_is_migrated(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE uploads (id TEXT PRIMARY KEY, original_filename TEXT, "
                      "stored_path TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL "
                      "CHECK (status IN ('queued','processing','done','failed')), "
                      "quality_report_json TEXT NOT NULL);")
    con.execute("INSERT INTO uploads VALUES ('u','n','p','2026-01-01','queued','{}')")
    con.commit(); con.close()
    monkeypatch.setenv(db.DB_ENV_VAR, str(path))
    assert db.get_upload("u")["error"] is None
