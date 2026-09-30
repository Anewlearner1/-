import sys
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.upload_quality import (  # noqa: E402
    CheckStatus,
    CourtCornerChecker,
    CheckResult,
    NotImplementedCourtCornerChecker,
    check_upload_quality,
)

from synth import (  # noqa: E402
    write_jittery_video,
    write_static_video,
    write_steady_textured_video,
)


@pytest.fixture
def skip_if_no_encoder():
    def _check(path):
        if not path.exists() or path.stat().st_size == 0:
            pytest.skip("此環境缺少 mp4 編碼器")
    return _check


# --------------------------------------------------------------------- fps

def test_high_fps_stable_video_passes_fps_check(tmp_path, skip_if_no_encoder):
    video = tmp_path / "good.mp4"
    try:
        write_steady_textured_video(video, fps=60, n_frames=90)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    skip_if_no_encoder(video)

    report = check_upload_quality(video)
    assert report.fps.status == CheckStatus.PASS
    assert report.fps.metrics["fps"] >= 59  # allow encoder rounding


def test_low_fps_video_fails_with_zh_message(tmp_path, skip_if_no_encoder):
    video = tmp_path / "low_fps.mp4"
    try:
        write_steady_textured_video(video, fps=30, n_frames=45)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    skip_if_no_encoder(video)

    report = check_upload_quality(video)
    assert report.fps.status == CheckStatus.FAIL
    assert report.fps.message_zh is not None
    assert "fps" in report.fps.message_zh.lower() or "幀率" in report.fps.message_zh
    assert report.passed is False
    assert report.fps.message_zh in report.messages_zh


# ------------------------------------------------------------ camera shake

def test_static_video_passes_camera_stability(tmp_path, skip_if_no_encoder):
    video = tmp_path / "steady.mp4"
    try:
        write_steady_textured_video(video, fps=60, n_frames=90)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    skip_if_no_encoder(video)

    report = check_upload_quality(video)
    assert report.camera_stability.status == CheckStatus.PASS
    assert report.camera_stability.metrics["mean_displacement_px"] < 4.0


def test_jittery_video_fails_camera_stability(tmp_path, skip_if_no_encoder):
    video = tmp_path / "shaky.mp4"
    try:
        write_jittery_video(video, fps=60, n_frames=90, max_shift=30)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    skip_if_no_encoder(video)

    report = check_upload_quality(video)
    assert report.camera_stability.status == CheckStatus.FAIL
    assert report.camera_stability.message_zh is not None
    assert report.passed is False


# --------------------------------------------------------- court corners

def test_default_court_corner_check_is_not_implemented_not_a_fake_pass(
    tmp_path, skip_if_no_encoder
):
    video = tmp_path / "any.mp4"
    try:
        write_steady_textured_video(video, fps=60, n_frames=90)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    skip_if_no_encoder(video)

    report = check_upload_quality(video)
    assert report.court_corners.status == CheckStatus.NOT_IMPLEMENTED
    assert report.court_corners.status != CheckStatus.PASS
    assert report.court_corners.message_zh is not None
    # A NOT_IMPLEMENTED court check alone must not block an otherwise-good
    # upload -- M0 cannot depend on cv-engineer's not-yet-built detector.
    assert report.passed is True


def test_court_corner_checker_is_pluggable(tmp_path, skip_if_no_encoder):
    video = tmp_path / "any2.mp4"
    try:
        write_steady_textured_video(video, fps=60, n_frames=90)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    skip_if_no_encoder(video)

    class AlwaysFailChecker(CourtCornerChecker):
        def check(self, video_path):
            return CheckResult(
                name="court_corners",
                status=CheckStatus.FAIL,
                detail="stub: pretend corners not found",
                message_zh="看不到球場四個角，請重新拍攝。",
            )

    report = check_upload_quality(video, court_corner_checker=AlwaysFailChecker())
    assert report.court_corners.status == CheckStatus.FAIL
    assert report.passed is False  # a real FAIL from the plugged-in checker does gate


def test_not_implemented_checker_never_returns_pass():
    # Direct unit check on the stub itself: regardless of input, it must
    # never claim success.
    result = NotImplementedCourtCornerChecker().check("/nonexistent/path.mp4")
    assert result.status == CheckStatus.NOT_IMPLEMENTED
    assert result.status != CheckStatus.PASS


# ------------------------------------------------------------------- misc

def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        check_upload_quality("/definitely/not/a/real/file.mp4")


def test_report_messages_zh_only_includes_non_pass_checks(tmp_path, skip_if_no_encoder):
    video = tmp_path / "good2.mp4"
    try:
        write_steady_textured_video(video, fps=60, n_frames=90)
    except RuntimeError:
        pytest.skip("此環境缺少 mp4 編碼器")
    skip_if_no_encoder(video)

    report = check_upload_quality(video)
    # fps + stability pass -> no zh message; court corners always emits one
    # (NOT_IMPLEMENTED) until a real checker is plugged in.
    assert report.fps.message_zh is None
    assert report.camera_stability.message_zh is None
    assert len(report.messages_zh) == 1
