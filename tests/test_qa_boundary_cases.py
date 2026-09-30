"""QA boundary-case tests for the upload-time quality gate.

Role: qa-engineer (technical-plan.md M2+). Goal is not to confirm the
happy path works -- `test_upload_quality.py` / `test_upload_api.py`
already do that -- but to deliberately try to trigger the failure modes
`backend/upload_quality.py`'s own module-level docstring admits to, plus
input-spec boundaries from technical-plan.md §5 that aren't yet covered.

Each test below maps to one of the five cases in the QA brief. Case 1
(slow pan) documents a *known, admitted* gap in upload_quality.py's
heuristic -- see docs/qa-findings.md for the writeup addressed to
cv-engineer/backend-engineer. It is not a new bug.
"""

import sys
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.upload_quality import (  # noqa: E402
    CheckStatus,
    check_upload_quality,
)

from synth import (  # noqa: E402
    write_jittery_video,
    write_slow_pan_video,
    write_static_video,
    write_steady_textured_video,
)


def _skip_if_no_encoder(path):
    if not Path(path).exists() or Path(path).stat().st_size == 0:
        pytest.skip("此環境缺少 mp4 編碼器")


# --------------------------------------------------------------------
# Case 1: slow, smooth pan -- documented known-bad behavior.
#
# upload_quality.py lines ~224-227: "A tripod with a slow pan or a fluid
# head being panned smoothly will look 'stable' per-frame ... and PASS
# even though the camera is not fixed." This test builds a real video
# that does exactly that and confirms the documented gap actually
# manifests, rather than just citing the docstring. See
# docs/qa-findings.md for the QA writeup / routing to cv-engineer and
# backend-engineer.
# --------------------------------------------------------------------

def test_slow_smooth_pan_incorrectly_passes_camera_stability(tmp_path):
    """KNOWN-BAD, DOCUMENTED BEHAVIOR (not a new bug -- see qa-findings.md).

    A camera panning smoothly at a small constant rate (1px/frame here,
    well under MAX_JITTER_PIXELS=4.0 per-pair) is not a fixed/tripod
    shot, but the heuristic only looks at frame-to-frame delta, so it
    reads as stable. This test pins down and reproduces that gap with a
    real synthetic video so it can't silently regress into "worse" (e.g.
    silently start flagging pans as bad some other way) without someone
    noticing this test's assertions change.
    """
    video = tmp_path / "slow_pan.mp4"
    try:
        write_slow_pan_video(video, fps=60, n_frames=90, shift_per_frame=1.0)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    _skip_if_no_encoder(video)

    report = check_upload_quality(video)

    # This assertion documents the CONFIRMED gap: a continuously panning
    # camera (never actually fixed) still gets a clean PASS.
    assert report.camera_stability.status == CheckStatus.PASS, (
        "If this now fails, the heuristic may have been improved to catch "
        "slow pans -- update docs/qa-findings.md accordingly, don't just "
        "flip this assertion."
    )
    metrics = report.camera_stability.metrics
    assert metrics["mean_displacement_px"] <= 4.0
    # Sanity: the pan is real motion (frames genuinely differ), it's just
    # small enough per-frame to stay under threshold every step.
    assert metrics["frame_pairs_analyzed"] > 0


# --------------------------------------------------------------------
# Case 2: fps exact boundary. MIN_FPS = 60.0; spec is `fps < MIN_FPS` fails.
# --------------------------------------------------------------------

def test_fps_59_fails_boundary(tmp_path):
    video = tmp_path / "fps59.mp4"
    try:
        write_steady_textured_video(video, fps=59, n_frames=59)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    _skip_if_no_encoder(video)

    report = check_upload_quality(video)
    assert report.fps.metrics["fps"] == pytest.approx(59.0, abs=0.5)
    assert report.fps.status == CheckStatus.FAIL
    assert report.passed is False


def test_fps_60_passes_boundary(tmp_path):
    video = tmp_path / "fps60.mp4"
    try:
        write_steady_textured_video(video, fps=60, n_frames=60)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    _skip_if_no_encoder(video)

    report = check_upload_quality(video)
    assert report.fps.metrics["fps"] == pytest.approx(60.0, abs=0.5)
    assert report.fps.status == CheckStatus.PASS


# --------------------------------------------------------------------
# Case 3: very short video (1-2 frames). Must fail gracefully via the
# n_pairs==0 (or n_pairs==1-but-nonsense) path, not crash. Also checked
# through the real API layer to confirm no 500.
# --------------------------------------------------------------------

def test_one_frame_video_fails_gracefully_not_crashes(tmp_path):
    video = tmp_path / "one_frame.mp4"
    try:
        write_static_video(video, fps=60, n_frames=1)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    _skip_if_no_encoder(video)

    report = check_upload_quality(video)  # must not raise
    assert report.camera_stability.status == CheckStatus.FAIL
    assert report.camera_stability.metrics["frame_pairs_analyzed"] == 0
    assert report.camera_stability.message_zh is not None
    assert report.passed is False


def test_two_frame_video_fails_gracefully_not_crashes(tmp_path):
    video = tmp_path / "two_frame.mp4"
    try:
        write_static_video(video, fps=60, n_frames=2)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    _skip_if_no_encoder(video)

    report = check_upload_quality(video)  # must not raise
    assert report.camera_stability.status == CheckStatus.FAIL
    assert report.passed is False


def test_one_frame_video_via_api_returns_200_not_500(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from backend.api.upload import app

    monkeypatch.setattr("backend.api.upload.UPLOAD_DIR", tmp_path / "uploads")
    client = TestClient(app)

    video = tmp_path / "one_frame.mp4"
    try:
        write_static_video(video, fps=60, n_frames=1)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    _skip_if_no_encoder(video)

    with open(video, "rb") as f:
        resp = client.post("/upload", files={"file": ("one_frame.mp4", f, "video/mp4")})

    # A degenerate-but-openable video is a QualityReport FAIL, not a
    # server error -- must come back as 200 with passed=False, never 500.
    assert resp.status_code == 200
    body = resp.json()
    assert body["passed"] is False
    assert "upload_id" not in body


# --------------------------------------------------------------------
# Case 4: corrupted/truncated video through the real /upload API.
# Must return the documented 422 error shape, not a 500 / unhandled
# exception.
# --------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from backend.api.upload import app

    monkeypatch.setattr("backend.api.upload.UPLOAD_DIR", tmp_path / "uploads")
    return TestClient(app)


def test_truncated_valid_video_returns_422_not_500(tmp_path, client):
    """Write a real, valid mp4, then truncate it mid-file (simulating an
    interrupted upload / flaky mobile connection), and confirm the API
    still returns the documented 422 invalid_video_file shape rather than
    an unhandled exception.
    """
    source = tmp_path / "source.mp4"
    try:
        write_steady_textured_video(source, fps=60, n_frames=90)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    _skip_if_no_encoder(source)

    data = source.read_bytes()
    truncated = data[: max(1, len(data) // 10)]  # keep first ~10%, drop the moov atom
    assert len(truncated) < len(data)

    import io
    resp = client.post(
        "/upload",
        files={"file": ("truncated.mp4", io.BytesIO(truncated), "video/mp4")},
    )

    assert resp.status_code == 422
    body = resp.json()
    assert "passed" not in body
    assert "checks" not in body
    assert body["error"] == "invalid_video_file"
    assert "detail" in body


def test_garbage_bytes_with_mp4_extension_returns_422_not_500(tmp_path, client):
    """Belt-and-suspenders on top of test_upload_api.py's equivalent case:
    a file that was never a video at all, just garbage bytes with a .mp4
    extension (e.g. a renamed non-video file), must also be a clean 422.
    """
    import io
    garbage = (b"\x00\x01\x02NOT_A_REAL_VIDEO_CONTAINER" * 50)
    resp = client.post(
        "/upload",
        files={"file": ("fake.mp4", io.BytesIO(garbage), "video/mp4")},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "invalid_video_file"


# --------------------------------------------------------------------
# Case 5: genuinely fast-moving/shaky synthetic video -- confirm a true
# positive (the check isn't broken in the "never fails" direction).
# Complements test_upload_quality.py's existing jittery-video test with
# a larger, more clearly-beyond-threshold jitter and explicit metric
# assertions.
# --------------------------------------------------------------------

def test_large_random_jitter_correctly_fails_camera_stability(tmp_path):
    video = tmp_path / "very_shaky.mp4"
    try:
        write_jittery_video(video, fps=60, n_frames=90, max_shift=40, seed=7)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    _skip_if_no_encoder(video)

    report = check_upload_quality(video)
    assert report.camera_stability.status == CheckStatus.FAIL
    metrics = report.camera_stability.metrics
    # A true positive: well beyond both thresholds, not a borderline case.
    assert (
        metrics["mean_displacement_px"] > 4.0
        or metrics["std_displacement_px"] > 6.0
    )
    assert report.camera_stability.message_zh is not None
    assert report.passed is False
