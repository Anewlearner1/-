"""Upload-time quality gate for Rally AI (technical-plan.md §5).

Runs cheap checks on a just-uploaded video *before* it is queued for the
batch-processing pipeline, so a user with unusable footage is told to
re-shoot immediately instead of burning GPU time on a job that cannot
succeed. This module owns the gate itself; it does not own the real
computer-vision detectors that some checks depend on (see CourtCornerChecker
below).

Checks implemented here:

1. Frame rate   -- real. Read via OpenCV metadata. technical-plan.md §5 wants
   >=60fps; below that the ball moves ~1m/frame and motion blur gets heavy.
2. Camera stability (fixed/tripod heuristic) -- real, but a heuristic. See
   the docstring on `_estimate_camera_jitter` for exactly what it measures
   and how it can be fooled. It is a screening signal, not a certified
   tripod detector.
3. Court corners visible -- NOT implemented here. This is cv-engineer's
   court-keypoint-detection work (milestone M4 in technical-plan.md §6),
   which doesn't exist yet at M0. This module defines the pluggable
   interface (`CourtCornerChecker`) and ships a stub implementation that
   always reports "not implemented" -- it must never report a fake pass,
   because a false "corners visible" would let bad footage through the
   gate and waste GPU time downstream.

Callers (the upload API handler) should treat `QualityReport.passed` as
"safe to enqueue". `messages_zh` holds one human-readable Traditional
Chinese re-shoot prompt per failed/blocked check, suitable for the
frontend to surface directly.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import cv2
except ImportError as exc:  # pragma: no cover - environment issue, not a test case
    raise ImportError(
        "opencv-python is required by backend.upload_quality; "
        "install it via requirements.txt"
    ) from exc


# --------------------------------------------------------------------------
# Result primitives
# --------------------------------------------------------------------------

class CheckStatus(str, Enum):
    """Outcome of a single quality check.

    PASS / FAIL are ordinary pass/fail results. NOT_IMPLEMENTED is for
    checks whose real logic does not exist yet (currently: court corners) --
    it is deliberately distinct from PASS so a caller can never mistake
    "we didn't check this" for "this is fine".
    """

    PASS = "pass"
    FAIL = "fail"
    NOT_IMPLEMENTED = "not_implemented"


@dataclass
class CheckResult:
    """Outcome of one individual check."""

    name: str
    status: CheckStatus
    detail: str  # short machine/log-friendly explanation (English)
    message_zh: Optional[str] = None  # human-facing re-shoot prompt, set iff not PASS
    metrics: dict = field(default_factory=dict)  # raw numbers behind the verdict

    @property
    def ok(self) -> bool:
        return self.status == CheckStatus.PASS


@dataclass
class QualityReport:
    """Aggregate result returned by `check_upload_quality`."""

    video_path: str
    fps: CheckResult
    camera_stability: CheckResult
    court_corners: CheckResult

    @property
    def checks(self) -> list:
        return [self.fps, self.camera_stability, self.court_corners]

    @property
    def passed(self) -> bool:
        """Whether the upload is safe to enqueue for batch processing.

        NOT_IMPLEMENTED checks do not count as failures on their own --
        otherwise no upload could ever pass until cv-engineer ships the
        real court-corner detector. Once that detector lands and is wired
        in via `CourtCornerChecker`, its FAIL results behave like any
        other check's and will gate uploads normally.
        """
        return all(c.status != CheckStatus.FAIL for c in self.checks)

    @property
    def messages_zh(self) -> list:
        """Human-readable Traditional Chinese prompts for every check that
        is not a clean PASS, in a stable order (fps, camera, court
        corners). Frontend surfaces these directly as re-shoot guidance.
        """
        return [c.message_zh for c in self.checks if c.message_zh]


# --------------------------------------------------------------------------
# Pluggable court-corner check
#
# The real detector is cv-engineer's work (court keypoint detection +
# homography, milestone M4). This interface exists so the upload pipeline
# can call *something* today and swap in the real implementation later
# without any caller-side changes.
# --------------------------------------------------------------------------

class CourtCornerChecker(abc.ABC):
    """Interface for "are all four court corners visible" detectors.

    Implement `check(video_path)` and return a CheckResult. A real
    implementation should inspect frame(s) for the four baseline/sideline
    intersection points (or equivalent court keypoints) and return PASS
    only when it actually found and localized them with a defensible
    confidence threshold.
    """

    name = "court_corners"

    @abc.abstractmethod
    def check(self, video_path: "str | Path") -> CheckResult:
        raise NotImplementedError


class NotImplementedCourtCornerChecker(CourtCornerChecker):
    """Default stub. Always reports NOT_IMPLEMENTED, never a fake PASS.

    Swap this out for the real cv-engineer detector by passing a different
    `CourtCornerChecker` instance to `check_upload_quality`.
    """

    def check(self, video_path: "str | Path") -> CheckResult:
        return CheckResult(
            name=self.name,
            status=CheckStatus.NOT_IMPLEMENTED,
            detail=(
                "Court-corner visibility detection is not implemented yet "
                "(depends on cv-engineer's court keypoint work, "
                "technical-plan.md M4). This check is a stub and always "
                "reports NOT_IMPLEMENTED; it never returns a fake pass."
            ),
            message_zh=(
                "球場四角偵測功能尚未完成，暫時無法自動確認畫面是否完整涵蓋球場四個角。"
                "請自行確認拍攝時腳架架設在底線後方、四個角盡量入鏡（球員仍需至少佔畫面高度 1/4），"
                "此項檢查結果不代表通過或未通過。"
            ),
        )


# --------------------------------------------------------------------------
# FPS check
# --------------------------------------------------------------------------

MIN_FPS = 60.0


def _check_fps(cap: "cv2.VideoCapture") -> CheckResult:
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    metrics = {"fps": fps, "min_fps": MIN_FPS}

    if fps <= 0:
        return CheckResult(
            name="fps",
            status=CheckStatus.FAIL,
            detail="Could not read a valid FPS value from the video container.",
            message_zh="無法讀取影片幀率，請確認檔案未損壞後重新上傳。",
            metrics=metrics,
        )

    if fps < MIN_FPS:
        return CheckResult(
            name="fps",
            status=CheckStatus.FAIL,
            detail=f"fps={fps:.1f} is below the {MIN_FPS:.0f}fps minimum; "
                   f"may have heavy motion blur.",
            message_zh=(
                f"偵測到影片幀率約 {fps:.0f} fps，低於建議的 {MIN_FPS:.0f} fps。"
                "幀率過低時球體每幀移動距離較大，容易造成嚴重模糊，"
                "請使用可拍攝 60fps 以上的模式重新拍攝。"
            ),
            metrics=metrics,
        )

    return CheckResult(
        name="fps",
        status=CheckStatus.PASS,
        detail=f"fps={fps:.1f} meets the {MIN_FPS:.0f}fps minimum.",
        metrics=metrics,
    )


# --------------------------------------------------------------------------
# Camera stability (fixed/tripod) heuristic
# --------------------------------------------------------------------------
#
# HONEST LIMITS OF THIS HEURISTIC (read before trusting it):
#
# What it actually measures: frame-to-frame global translation, estimated
# by phase correlation between consecutive grayscale frames, sampled across
# the clip. A tripod-mounted, non-panning camera should show small,
# low-variance displacement between frames. A handheld camera typically
# shows larger and/or more variable displacement from hand jitter.
#
# What it does NOT detect / can get wrong:
# - [Finding 1, docs/qa-findings.md -- FIXED] A tripod with a slow pan or a
#   fluid head being panned smoothly used to look "stable" per-frame (small
#   frame-to-frame delta) and PASS even though the camera is not fixed, since
#   the original heuristic only looked at per-pair jitter magnitude. We now
#   also track the *cumulative, directionally-consistent* drift across the
#   sampled sequence (vector sum of per-pair displacements, and how much of
#   the total travelled distance that vector sum represents -- see
#   `_estimate_camera_jitter`'s docstring for the exact math) and FAIL when
#   that drift is large and consistently in one direction, which is what a
#   pan looks like but random jitter does not (random jitter's vector sum
#   tends to cancel out). This is still a heuristic, not a certified
#   fixed-vs-panning classifier -- see the remaining gaps below.
# - A handheld camera held very still (e.g. braced against a wall) can
#   pass this check despite not being on a tripod.
# - Large moving subjects filling the frame (e.g. a player very close to
#   the lens) can bias the phase-correlation estimate and register as
#   apparent camera motion that isn't there.
# - It is computed on a downsampled, evenly-spaced subset of frames for
#   speed, not the whole video, so brief shake outside the sampled frames
#   is missed.
# - The new cumulative-drift/pan check can still miss a slow pan that
#   changes direction partway through the sampled sequence (e.g. panning
#   right then left) -- the vector sum partially or fully cancels even
#   though the camera was never fixed. It can also, in principle, flag a
#   camera that is legitimately being slowly and deliberately re-aimed once
#   (a single intentional reposition) as a "sustained pan" even though
#   that's arguably fine for a short clip; it does not try to distinguish
#   "repositioning once" from "continuously panning to follow play".
# - Thresholds below were chosen heuristically (not fit against labeled
#   handheld/tripod/panning footage) and will need calibration against real
#   uploads once available.
#
# In short: this is a cheap pre-filter to catch obviously shaky handheld
# footage and sustained one-directional panning before it wastes GPU time,
# not a certified "was this on a tripod" classifier. Do not present it to
# users as more precise than "camera may not be steady enough."

MAX_JITTER_PIXELS = 4.0  # mean frame-to-frame displacement magnitude, in pixels
MAX_JITTER_STD_PIXELS = 6.0  # variability of that displacement
SAMPLE_FRAME_COUNT = 60  # cap on frames analyzed, evenly spaced through the clip

# Finding 1 fix: cumulative-drift ("sustained pan") thresholds. See
# `_estimate_camera_jitter` for how `cumulative_drift_px` and
# `directional_consistency` are computed. Both must be exceeded together --
# a large cumulative drift alone can happen from pure jitter by chance over
# enough frames, and high directional consistency alone on a tiny drift is
# just noise -- so we require both the distance *and* the straightness to be
# notable before calling it a pan.
MAX_CUMULATIVE_DRIFT_PIXELS = 15.0
MIN_PAN_DIRECTIONAL_CONSISTENCY = 0.6


def _estimate_camera_jitter(
    cap: "cv2.VideoCapture",
) -> "tuple[float, float, int, float, float]":
    """Returns (mean_displacement_px, std_displacement_px, n_pairs_used,
    cumulative_drift_px, directional_consistency).

    Uses cv2.phaseCorrelate on grayscale frame pairs sampled evenly across
    the video to get a per-pair displacement vector (dx, dy).

    - mean/std_displacement_px summarize the per-pair displacement
      *magnitude* only, as before -- this is what catches high-frequency
      jitter (handheld shake), but is blind to a slow, smooth pan whose
      per-pair step is small.
    - cumulative_drift_px is the magnitude of the vector sum of every
      per-pair (dx, dy): |sum(dx), sum(dy)|. A slow pan's steps all point
      the same way, so this sum grows roughly linearly with the number of
      sampled frames even though each step is tiny. Random jitter's steps
      point in random directions, so this sum tends to stay small relative
      to the distance actually travelled.
    - directional_consistency is cumulative_drift_px divided by the sum of
      the per-pair displacement *magnitudes* (i.e. the straight-line
      distance between first and last sampled frame divided by the total
      path length walked to get there). It's 1.0 for a perfectly straight,
      one-directional pan and tends toward 0 for motion that cancels out
      (jitter, or a pan that reverses direction). This is the standard
      "mean resultant length" used for circular/directional data.

    See the module-level comment above for what this can and cannot detect.
    """
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total_frames <= 1:
        return 0.0, 0.0, 0, 0.0, 0.0

    n_samples = min(SAMPLE_FRAME_COUNT, total_frames)
    indices = sorted(set(
        int(round(i * (total_frames - 1) / max(n_samples - 1, 1)))
        for i in range(n_samples)
    ))

    vectors = []
    prev_gray = None
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev_gray is not None and prev_gray.shape == gray.shape:
            try:
                (dx, dy), _response = cv2.phaseCorrelate(prev_gray, gray)
                vectors.append((float(dx), float(dy)))
            except cv2.error:
                pass
        prev_gray = gray

    if not vectors:
        return 0.0, 0.0, 0, 0.0, 0.0

    vec_arr = np.array(vectors, dtype=np.float64)
    magnitudes = np.hypot(vec_arr[:, 0], vec_arr[:, 1])
    sum_vector = vec_arr.sum(axis=0)
    cumulative_drift = float(np.hypot(sum_vector[0], sum_vector[1]))
    total_path_length = float(magnitudes.sum())
    directional_consistency = (
        cumulative_drift / total_path_length if total_path_length > 0 else 0.0
    )

    return (
        float(magnitudes.mean()),
        float(magnitudes.std()),
        len(vectors),
        cumulative_drift,
        directional_consistency,
    )


def _check_camera_stability(cap: "cv2.VideoCapture") -> CheckResult:
    (mean_disp, std_disp, n_pairs,
     cumulative_drift, directional_consistency) = _estimate_camera_jitter(cap)
    metrics = {
        "mean_displacement_px": mean_disp,
        "std_displacement_px": std_disp,
        "frame_pairs_analyzed": n_pairs,
        "max_mean_px": MAX_JITTER_PIXELS,
        "max_std_px": MAX_JITTER_STD_PIXELS,
        "cumulative_drift_px": cumulative_drift,
        "directional_consistency": directional_consistency,
        "max_cumulative_drift_px": MAX_CUMULATIVE_DRIFT_PIXELS,
        "min_pan_directional_consistency": MIN_PAN_DIRECTIONAL_CONSISTENCY,
    }

    if n_pairs == 0:
        return CheckResult(
            name="camera_stability",
            status=CheckStatus.FAIL,
            detail="Could not analyze enough frames to estimate camera motion.",
            message_zh="影片幀數過少，無法評估運鏡穩定度，請確認影片內容後重新上傳。",
            metrics=metrics,
        )

    is_jittery = mean_disp > MAX_JITTER_PIXELS or std_disp > MAX_JITTER_STD_PIXELS
    # Finding 1 fix (docs/qa-findings.md): a slow, smooth pan has small
    # per-pair displacement but sums to a large, directionally-consistent
    # cumulative drift that per-pair jitter alone doesn't catch. Flag it as
    # a separate "sustained pan" failure. See the module docstring and
    # `_estimate_camera_jitter`'s docstring for exactly what these measure
    # and their known limits.
    is_sustained_pan = (
        cumulative_drift > MAX_CUMULATIVE_DRIFT_PIXELS
        and directional_consistency > MIN_PAN_DIRECTIONAL_CONSISTENCY
    )

    if is_jittery:
        return CheckResult(
            name="camera_stability",
            status=CheckStatus.FAIL,
            detail=(
                f"mean_displacement={mean_disp:.2f}px, std={std_disp:.2f}px "
                f"exceeds thresholds (mean<={MAX_JITTER_PIXELS}, "
                f"std<={MAX_JITTER_STD_PIXELS}); likely handheld/shaky camera. "
                "This is a heuristic screen, not a certified tripod detector "
                "(see module docstring for limits)."
            ),
            message_zh=(
                "偵測到畫面晃動幅度較大，可能是手持拍攝而非腳架固定。"
                "請使用腳架固定機位後重新拍攝（此為啟發式偵測，僅供參考，"
                "非絕對準確）。"
            ),
            metrics=metrics,
        )

    if is_sustained_pan:
        return CheckResult(
            name="camera_stability",
            status=CheckStatus.FAIL,
            detail=(
                f"cumulative_drift={cumulative_drift:.2f}px with directional "
                f"consistency={directional_consistency:.2f} "
                f"(> {MAX_CUMULATIVE_DRIFT_PIXELS}px and "
                f"> {MIN_PAN_DIRECTIONAL_CONSISTENCY} respectively), even "
                f"though per-pair displacement (mean={mean_disp:.2f}px, "
                f"std={std_disp:.2f}px) looked stable; likely a sustained "
                "pan rather than a fixed camera. This is a heuristic screen, "
                "not a certified tripod detector (see module docstring for "
                "limits)."
            ),
            message_zh=(
                "雖然每幀之間的晃動幅度不大，但偵測到持續朝同一方向的運鏡"
                "（例如緩慢橫搖），機位並非固定不動。請使用腳架固定機位後"
                "重新拍攝（此為啟發式偵測，僅供參考，非絕對準確）。"
            ),
            metrics=metrics,
        )

    return CheckResult(
        name="camera_stability",
        status=CheckStatus.PASS,
        detail=(
            f"mean_displacement={mean_disp:.2f}px, std={std_disp:.2f}px "
            f"within thresholds; cumulative_drift={cumulative_drift:.2f}px "
            f"with directional_consistency={directional_consistency:.2f}; "
            "consistent with a fixed camera. Heuristic only."
        ),
        metrics=metrics,
    )


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def check_upload_quality(
    video_path: "str | Path",
    court_corner_checker: Optional[CourtCornerChecker] = None,
) -> QualityReport:
    """Run the upload-time quality gate on a video before it is queued.

    Parameters
    ----------
    video_path:
        Path to the uploaded video file.
    court_corner_checker:
        Pluggable court-corner-visibility detector. Defaults to the stub
        that always reports NOT_IMPLEMENTED. Pass an instance of a real
        `CourtCornerChecker` subclass (cv-engineer's future M4 work) to
        swap in real detection without touching any caller code.

    Raises
    ------
    FileNotFoundError
        If `video_path` does not exist.
    ValueError
        If the file exists but OpenCV cannot open it as a video.
    """
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"video file not found: {path}")

    checker = court_corner_checker or NotImplementedCourtCornerChecker()

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        cap.release()
        raise ValueError(f"could not open video file: {path}")

    try:
        fps_result = _check_fps(cap)
        stability_result = _check_camera_stability(cap)
    finally:
        cap.release()

    corners_result = checker.check(path)

    return QualityReport(
        video_path=str(path),
        fps=fps_result,
        camera_stability=stability_result,
        court_corners=corners_result,
    )
