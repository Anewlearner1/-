"""labeling/label_server.py endpoints via TestClient."""
import json

import pytest
from fastapi.testclient import TestClient

from synth import write_slow_pan_video


@pytest.fixture
def env(tmp_path, monkeypatch):
    from labeling import label_server
    vids = tmp_path / "videos"
    vids.mkdir()
    monkeypatch.setenv("RALLY_VIDEO_DIR", str(vids))
    monkeypatch.setenv("RALLY_LABEL_DIR", str(tmp_path / "labels"))
    monkeypatch.setenv("RALLY_LABEL_PROXY_DIR", str(tmp_path / "proxies"))
    monkeypatch.setattr(label_server, "BACKUP_DIR", tmp_path / "backups")
    write_slow_pan_video(vids / "clip.one.mp4", 30.0, 40)
    (tmp_path / "secret.mp4").write_bytes(b"x")
    return {"client": TestClient(label_server.app), "tmp": tmp_path}


def _payload(**kw):
    body = {"fps": 30.0, "marks": [{"frame": 30, "stroke": "backhand"},
                                   {"frame": 5, "stroke": "forehand"}],
            "complete": True, "racket_hand": "left"}
    body.update(kw)
    return body


def test_page_and_scripts_served(env):
    c = env["client"]
    r = c.get("/")
    assert r.status_code == 200 and "解碼器幀" in r.text
    assert c.get("/label.js").status_code == 200
    assert c.get("/label_logic.js").status_code == 200


def test_list_proxy_save_reload_round_trip(env):
    c, tmp = env["client"], env["tmp"]
    vids = c.get("/api/videos").json()["videos"]
    assert vids == [{"name": "clip.one.mp4", "has_label": False, "n_contacts": 0,
                     "complete": False, "frame_numbering": None}]
    assert c.get("/api/videos/clip.one.mp4/label").status_code == 404

    r = c.get("/api/videos/clip.one.mp4/proxy")
    assert r.status_code == 200 and r.headers["content-type"] == "video/webm"
    assert r.content[:4] == b"\x1a\x45\xdf\xa3"                  # EBML magic
    rng = c.get("/api/videos/clip.one.mp4/proxy", headers={"Range": "bytes=0-99"})
    assert rng.status_code == 206 and len(rng.content) == 100
    info = c.get("/api/videos/clip.one.mp4/info").json()
    assert info["frame_count"] == 40 and info["fps"] == 30.0

    r = c.post("/api/videos/clip.one.mp4/label", json=_payload())
    assert r.status_code == 200, r.text
    label = json.loads((tmp / "labels" / "clip.one.json").read_text())
    assert label == {"video_id": "clip.one", "fps": 30.0, "contact_frames": [5, 30],
                     "stroke_labels": ["forehand", "backhand"], "racket_hand": "left",
                     "source": "real"}
    meta = json.loads((tmp / "labels" / "clip.one.meta.json").read_text())
    assert meta["complete"] is True and meta["frame_numbering"] == "opencv_decoder"
    assert meta["source"] == "owner_labeling_page" and meta["racket_hand"] == "left"
    assert meta["reviewer"] == "project owner" and len(meta["date"]) == 10

    got = c.get("/api/videos/clip.one.mp4/label").json()
    assert got["label"] == label and got["meta"] == meta
    v = c.get("/api/videos").json()["videos"][0]
    assert v["has_label"] and v["complete"] and v["n_contacts"] == 2

    # The eval loaders accept it.
    from ml.eval_shot_timing import load_label
    loaded = load_label(tmp / "labels" / "clip.one.json")
    assert loaded["contact_frames"] == [5, 30] and loaded["complete"] is True

    # Saving again backs up the previous version outside labeling/labels.
    r = c.post("/api/videos/clip.one.mp4/label", json=_payload(complete=False, marks=[]))
    assert r.status_code == 200
    assert len(list((tmp / "backups").glob("clip.one.json.*"))) == 1
    assert json.loads((tmp / "labels" / "clip.one.meta.json").read_text())["complete"] is False


@pytest.mark.parametrize("name", ["../secret.mp4", "..%2Fsecret.mp4", "%2E%2E", "nope.mp4"])
def test_path_traversal_and_unknown_names_rejected(env, name):
    c = env["client"]
    for url in (f"/api/videos/{name}/proxy", f"/api/videos/{name}/label"):
        assert c.get(url).status_code in (400, 404)
    assert c.post(f"/api/videos/{name}/label", json=_payload()).status_code in (400, 404)
    assert not (env["tmp"] / "labels" / "secret.json").exists()


@pytest.mark.parametrize("body,needle", [
    (_payload(marks=[{"frame": 7, "stroke": "forehand"}, {"frame": 7, "stroke": "backhand"}]),
     "duplicate"),
    (_payload(marks=[{"frame": 40, "stroke": "forehand"}]), "out of range"),
    (_payload(marks=[{"frame": -1, "stroke": "forehand"}]), "out of range"),
    (_payload(marks=[{"frame": 3, "stroke": "smash"}]), ""),
    (_payload(racket_hand="both"), ""),
    (_payload(fps=60.0), "fps"),
])
def test_invalid_labels_rejected_with_422(env, body, needle):
    r = env["client"].post("/api/videos/clip.one.mp4/label", json=body)
    assert r.status_code == 422 and needle in r.text
    assert not (env["tmp"] / "labels" / "clip.one.json").exists()
