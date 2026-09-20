"""Integration test for the CLI.

MediaPipe inference is stubbed out -- a synthetic stick figure is not a person
and the tracker would (correctly) find nothing in it. Everything else runs for
real: argument parsing, the measurement pipeline, the terminal table and the
JSON output.
"""
import json

import numpy as np
import pytest

from synth import synth_swing_sequence
from tennis_coach import cli

cv2 = pytest.importorskip("cv2")


@pytest.fixture(scope="module")
def synthetic():
    return synth_swing_sequence(n_swings=2)


@pytest.fixture(scope="module")
def video_file(synthetic, tmp_path_factory):
    """A real (if featureless) mp4, so the file-exists checks are genuine."""
    path = tmp_path_factory.mktemp("video") / "swing.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             synthetic.fps, (synthetic.width, synthetic.height))
    if not writer.isOpened():
        pytest.skip("此環境缺少 mp4 編碼器")
    for _ in range(synthetic.n_frames):
        writer.write(np.full((synthetic.height, synthetic.width, 3), 18, np.uint8))
    writer.release()
    return path


@pytest.fixture
def stub_pose(monkeypatch, synthetic):
    monkeypatch.setattr("tennis_coach.pose.estimate_pose",
                        lambda video, **kw: synthetic)


def test_speed_prints_a_row_per_swing(stub_pose, video_file, capsys):
    assert cli.main(["speed", str(video_file), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "拍頭速度" in out and "擊球初速" in out and "擊球角度" in out
    assert "km/h" in out
    # Two swings in the fixture, so two numbered rows.
    assert "  1" in out and "  2" in out


def test_speed_writes_json(stub_pose, video_file, tmp_path):
    out_file = tmp_path / "speed.json"
    assert cli.main(["speed", str(video_file), "--quiet",
                     "--json", str(out_file)]) == 0

    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["hand"] in ("right", "left")
    assert data["racket_length_m"] > 0
    assert len(data["swings"]) == 2
    for swing in data["swings"]:
        assert swing["racket_speed_kmh"] > 0
        assert swing["wrist_speed_kmh"] > 0
        # No real footage behind the stub, so the ball half must come back
        # empty rather than inventing a number.
        assert swing["ball_speed_kmh"] is None
        assert swing["notes"]


def test_racket_length_flag_changes_the_reported_speed(stub_pose, video_file, tmp_path):
    from tennis_coach import kinetics
    original = kinetics.RACKET_LENGTH_M
    try:
        short = tmp_path / "short.json"
        long = tmp_path / "long.json"
        cli.main(["speed", str(video_file), "--quiet", "--racket-length", "0.30",
                  "--json", str(short)])
        cli.main(["speed", str(video_file), "--quiet", "--racket-length", "0.80",
                  "--json", str(long)])
    finally:
        kinetics.RACKET_LENGTH_M = original

    a = json.loads(short.read_text(encoding="utf-8"))["swings"][0]["racket_speed_kmh"]
    b = json.loads(long.read_text(encoding="utf-8"))["swings"][0]["racket_speed_kmh"]
    assert b > a


def test_an_absurd_racket_length_is_rejected(video_file, capsys):
    assert cli.main(["speed", str(video_file), "--racket-length", "12"]) == 2
    assert "--racket-length" in capsys.readouterr().err


def test_missing_video_is_reported(tmp_path, capsys):
    assert cli.main(["speed", str(tmp_path / "nope.mp4")]) == 2
    assert "找不到影片檔" in capsys.readouterr().err


def test_a_motionless_clip_exits_nonzero(monkeypatch, video_file, capsys):
    frozen = synth_swing_sequence(n_swings=1)
    frozen.world[:] = frozen.world[0]
    monkeypatch.setattr("tennis_coach.pose.estimate_pose", lambda video, **kw: frozen)

    assert cli.main(["speed", str(video_file), "--quiet"]) == 1
    assert "未偵測到揮拍動作" in capsys.readouterr().err
