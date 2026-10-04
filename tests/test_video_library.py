"""Tests for the server-side video folder (backend/library.py)."""
import json

import pytest

from backend import library
from synth import write_steady_textured_video


@pytest.fixture
def folder(tmp_path, monkeypatch):
    d = tmp_path / "videos"
    monkeypatch.setenv(library.VIDEO_DIR_ENV, str(d))
    return library.video_dir()


def test_folder_is_created_and_overridable(tmp_path, monkeypatch):
    target = tmp_path / "nested" / "vids"
    monkeypatch.setenv(library.VIDEO_DIR_ENV, str(target))
    assert library.video_dir() == target and target.is_dir()


def test_list_videos_filters_by_extension_and_sorts(folder):
    (folder / "b.mp4").write_bytes(b"x")
    (folder / "a.MOV").write_bytes(b"x")
    (folder / "notes.txt").write_text("hi")
    (folder / "sub").mkdir()
    assert [p.name for p in library.list_videos()] == ["a.MOV", "b.mp4"]


def test_ingest_copies_and_sanitizes_the_name(folder, tmp_path):
    src = write_steady_textured_video(tmp_path / "my clip (1).mp4", 60.0, 5)
    dest = library.ingest(src)
    assert dest.parent == folder
    assert dest.name == "my_clip_1_.mp4"
    assert dest.read_bytes() == src.read_bytes()


def test_ingest_never_overwrites_existing_footage(folder, tmp_path):
    src = write_steady_textured_video(tmp_path / "clip.mp4", 60.0, 5)
    first = library.ingest(src)
    second = library.ingest(src)
    assert first != second and first.exists() and second.exists()


def test_ingest_rejects_missing_and_non_video(folder, tmp_path):
    with pytest.raises(FileNotFoundError):
        library.ingest(tmp_path / "nope.mp4")
    txt = tmp_path / "a.txt"
    txt.write_text("x")
    with pytest.raises(ValueError):
        library.ingest(txt)


@pytest.mark.parametrize("bad", ["../secret.mp4", "a/b.mp4", "", "..", "/etc/passwd"])
def test_resolve_video_refuses_anything_but_a_plain_name(folder, bad):
    with pytest.raises(ValueError):
        library.resolve_video(bad)


def test_resolve_video_finds_a_real_file_and_reports_missing(folder, tmp_path):
    library.ingest(write_steady_textured_video(tmp_path / "ok.mp4", 60.0, 5))
    assert library.resolve_video("ok.mp4").name == "ok.mp4"
    with pytest.raises(FileNotFoundError):
        library.resolve_video("absent.mp4")


def test_probe_reads_real_metadata(tmp_path):
    p = write_steady_textured_video(tmp_path / "v.mp4", 60.0, 30,
                                    width=320, height=240)
    info = library.probe(p)
    assert (info.width, info.height, info.frames) == (320, 240, 30)
    assert info.fps == pytest.approx(60.0, abs=0.5)
    assert info.duration_s == pytest.approx(0.5, abs=0.05)
    assert info.orientation == "landscape"


def test_scan_reports_quality_and_survives_a_corrupt_file(folder, tmp_path):
    library.ingest(write_steady_textured_video(tmp_path / "good.mp4", 60.0, 30))
    library.ingest(write_steady_textured_video(tmp_path / "slow.mp4", 30.0, 30))
    (folder / "broken.mp4").write_bytes(b"not a video")

    by_name = {r["name"]: r for r in library.scan()}
    assert by_name["good.mp4"]["quality"]["passed"] is True
    assert by_name["slow.mp4"]["quality"]["passed"] is False
    assert by_name["slow.mp4"]["quality"]["checks"]["fps"] == "fail"
    assert "error" in by_name["broken.mp4"]
    assert "quality" not in by_name["broken.mp4"]


def test_cli_scan_prints_json(folder, tmp_path, capsys):
    library.ingest(write_steady_textured_video(tmp_path / "good.mp4", 60.0, 30))
    assert library.main(["scan"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["video_dir"] == str(folder)
    assert out["videos"][0]["name"] == "good.mp4"


def test_cli_ingest_reports_failures_with_nonzero_exit(folder, tmp_path, capsys):
    assert library.main(["ingest", str(tmp_path / "missing.mp4")]) == 1
    assert "skip" in capsys.readouterr().err
