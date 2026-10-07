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
    assert db.get_upload("u")["racket_hand"] is None


def test_strokes_are_labeled_only_with_the_flag_and_a_given_hand(env):
    given = db.create_upload("h.mp4", str(env), {"passed": True}, racket_hand="right")
    missing = _queue(env, "n.mp4")
    worker.process_upload(given)                                  # flag off
    assert all(s["fh_bh_label"] is None for s in db.list_shots(given))
    worker.process_upload(missing, classify_strokes=True)         # no hand: never inferred
    assert all(s["fh_bh_label"] is None for s in db.list_shots(missing))
    worker.process_upload(given, classify_strokes=True)
    shots = db.list_shots(given)
    assert shots and all(s["fh_bh_label"] in ("forehand", "backhand", "other", None) for s in shots)
    assert "stroke_rule(hand=right,given)" in shots[0]["source"]
    assert all((s["fh_bh_label"] is None) == (s["fh_bh_confidence"] is None) for s in shots)


def test_a_classifier_error_keeps_the_detected_shots(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("classifier broke")
    monkeypatch.setattr("ml.stroke_classification.classify_shots", boom)
    uid = db.create_upload("h.mp4", str(env), {"passed": True}, racket_hand="left")
    assert worker.process_upload(uid, classify_strokes=True) == 3
    assert db.get_upload(uid)["status"] == "done"
    shots = db.list_shots(uid)
    assert all(s["fh_bh_label"] is None for s in shots)
    assert "stroke_rule_failed(RuntimeError)" in shots[0]["source"]


def test_invalid_racket_hand_is_rejected_by_the_db(env):
    with pytest.raises(ValueError):
        db.create_upload("x.mp4", str(env), {}, racket_hand="both")


def test_unknown_stroke_labels_are_stored_as_null(env):
    from ml.shot_timing import ShotEvent
    uid = _queue(env)
    ev = [ShotEvent(10, 0.33, 1.0, "right"), ShotEvent(50, 1.67, 1.0, "right")]
    result = type("R", (), {"events": ev})()
    db.insert_shots_from_result(uid, result, stroke_labels=["backhand", "unknown"],
                                stroke_confidences=[0.3, 0.0])
    shots = db.list_shots(uid)
    assert [s["fh_bh_label"] for s in shots] == ["backhand", None]
    assert [s["fh_bh_status"] for s in shots] == ["labeled", "undetermined"]
    assert [s["fh_bh_confidence"] for s in shots] == [0.3, None]
    db.insert_shots_from_result(uid, result)
    assert [s["fh_bh_status"] for s in db.list_shots(uid)] == ["not_analyzed"] * 2
    with pytest.raises(ValueError):
        db.insert_shots_from_result(uid, result, stroke_labels=["backhand"])


def test_an_old_shots_table_gets_the_confidence_column(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "old2.sqlite3"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE shots (id INTEGER PRIMARY KEY AUTOINCREMENT, upload_id TEXT NOT NULL, "
                      "shot_index INTEGER NOT NULL, contact_frame INTEGER NOT NULL, contact_time_s REAL NOT NULL, "
                      "peak_speed REAL NOT NULL, wrist TEXT, stroke_label TEXT, ball_speed_kmh REAL, "
                      "source TEXT NOT NULL, UNIQUE (upload_id, shot_index));")
    con.execute("INSERT INTO shots (upload_id, shot_index, contact_frame, contact_time_s, peak_speed, source) "
                "VALUES ('u', 0, 1, 0.1, 1.0, 's')")
    con.commit(); con.close()
    monkeypatch.setenv(db.DB_ENV_VAR, str(path))
    assert db.list_shots("u")[0]["fh_bh_confidence"] is None
