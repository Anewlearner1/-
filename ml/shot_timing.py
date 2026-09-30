"""M2 (partial): shot-timing (contact) detection from the pose swing-speed
signal.

The technical plan (docs/technical-plan.md §3, row "擊球時間點") specifies
shot-timing detection as: ball trajectory direction change + pose swing-speed
peak, combined -- "no off-the-shelf solution exists, needs custom
development". Only the pose half is implemented here.

BLOCKED / not implemented: the ball-trajectory half. There is no ball
tracking in this codebase yet (M4/M5, TrackNet-based, GPU-dependent, out of
scope for this GPU-less sandbox -- see docs/technical-plan.md §3 and §7).
Once cv-engineer delivers ball tracking, a real detector here should fuse a
ball direction-change signal with the wrist-speed peaks below (e.g. requiring
a peak to coincide with, or closely precede, a trajectory reversal) rather
than trusting the pose signal alone -- the pose signal alone will have false
positives on any fast arm motion that isn't a stroke (e.g. a big split-step
recovery, a ball toss on a serve). See ml/README.md for the current status
and exactly what's still needed before this can be trusted on real footage.

Method (pose-swing-speed half only)
------------------------------------
Adapted from the general approach in the sibling tennis-form-coach project's
tennis_coach/segment.py (find_peaks over a smoothed wrist-speed trace), but
written fresh for this codebase: simpler (frame-count-only output, no swing
phase segmentation, no 3D geometry), and it does not assume which hand is
the racket hand -- it takes the faster-moving wrist at each frame, since
Rally AI's player_selection stage has no notion of handedness either.

    per-frame wrist (L, R) pixel positions
        --> per-wrist speed (pixels / second, finite difference)
        --> combined speed = max(L speed, R speed) per frame
        --> Savitzky-Golay smoothing
        --> scipy.signal.find_peaks (prominence + min-separation gated)
        --> one ShotEvent per accepted peak, contact_frame = the peak frame
"""
from __future__ import annotations

import dataclasses

import numpy as np
from scipy.signal import find_peaks, savgol_filter

from cv.pose_overlay import L_WRIST, R_WRIST, PlayerLandmarkSequence


@dataclasses.dataclass(frozen=True)
class ShotEvent:
    """One detected shot (contact) event."""

    contact_frame: int
    contact_time_s: float
    peak_speed: float          # pixels / second, smoothed
    wrist: str                 # "left" or "right" -- whichever was faster at the peak


@dataclasses.dataclass(frozen=True)
class ShotTimingResult:
    """Full output of ``detect_shots``: the events plus the signal they were
    found on, for debugging/plotting/comparison against ground truth."""

    events: list[ShotEvent]
    speed: np.ndarray          # smoothed combined wrist speed, pixels/s, per frame
    fps: float

    @property
    def shot_count(self) -> int:
        return len(self.events)


def _wrist_speed(landmarks: np.ndarray, wrist_idx: int, fps: float) -> np.ndarray:
    """Per-frame speed (pixels/second) of one landmark, via finite difference.

    NaN frames (no detected player) propagate as NaN; they are handled by
    the caller (treated as "no motion measurable" and filled with 0 after
    combining both wrists, so a short detection gap doesn't itself look
    like a swing peak).
    """
    pos = landmarks[:, wrist_idx, :]
    vel = np.full(pos.shape[0], np.nan)
    if pos.shape[0] < 2:
        return vel
    diffs = np.linalg.norm(np.diff(pos, axis=0), axis=1) * fps
    vel[1:] = diffs
    vel[0] = diffs[0] if diffs.size else np.nan
    return vel


def combined_wrist_speed(seq: PlayerLandmarkSequence) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame speed of whichever wrist is moving faster, and which one.

    We deliberately don't assume handedness (a right-handed forehand moves
    the right wrist fastest, a left-handed one the left, and a two-handed
    backhand can make either the dominant one depending on the frame) --
    same reasoning as cv/player_selection.py not modelling player identity.

    Returns:
        speed: (frame_count,) combined speed, pixels/second. NaN frames (both
            wrists undetected) are filled with 0.0 so a detection gap reads
            as "no motion" rather than poisoning peak-finding with NaN.
        which: (frame_count,) array of "left"/"right"/"" (empty where neither
            wrist was measurable that frame).
    """
    left = _wrist_speed(seq.landmarks, L_WRIST, seq.fps)
    right = _wrist_speed(seq.landmarks, R_WRIST, seq.fps)

    left_nan = np.isnan(left)
    right_nan = np.isnan(right)

    combined = np.nanmax(
        np.stack([np.where(left_nan, -np.inf, left),
                  np.where(right_nan, -np.inf, right)]),
        axis=0,
    )
    both_missing = left_nan & right_nan
    combined = np.where(both_missing, 0.0, combined)

    which = np.where(both_missing, "",
                      np.where(np.where(left_nan, -np.inf, left)
                               >= np.where(right_nan, -np.inf, right),
                               "left", "right"))
    return combined, which


def detect_shots(
    seq: PlayerLandmarkSequence,
    *,
    min_peak_ratio: float = 0.35,
    min_separation_s: float = 0.35,
    smooth_window_s: float = 0.15,
) -> ShotTimingResult:
    """Detect shot (contact) events from a player's landmark time series.

    Args:
        seq: per-frame landmarks, e.g. from
            ``cv.pose_overlay.extract_player_landmarks``.
        min_peak_ratio: a candidate peak must reach at least this fraction
            of the single fastest peak in the clip to count as a shot
            (mirrors tennis-form-coach's ``find_swings`` default of 0.35 --
            filters out small arm twitches relative to the clip's real
            swings).
        min_separation_s: minimum time between two accepted contacts, in
            seconds -- prevents one swing's speed curve (which can wobble
            near its true peak) from being counted twice.
        smooth_window_s: Savitzky-Golay smoothing window, in seconds --
            large enough to remove per-frame landmark jitter without
            blurring out a genuine swing peak (a full swing lasts several
            hundred ms; see tests/synth_pose.py's synthetic timings).

    Returns:
        ShotTimingResult with one ShotEvent per detected contact, ordered by
        frame, plus the smoothed speed signal they were found on.
    """
    fps = seq.fps
    n = seq.frame_count
    if n < 5:
        return ShotTimingResult(events=[], speed=np.zeros(n), fps=fps)

    speed, which = combined_wrist_speed(seq)

    window = max(5, int(round(smooth_window_s * fps)))
    if window % 2 == 0:
        window += 1
    window = min(window, n if n % 2 == 1 else n - 1)
    if window < 5:
        smoothed = speed.astype(float)
    else:
        smoothed = savgol_filter(speed, window_length=window, polyorder=2)
    smoothed = np.clip(smoothed, 0.0, None)

    top = float(smoothed.max())
    if top <= 1e-9:
        return ShotTimingResult(events=[], speed=smoothed, fps=fps)

    peaks, _ = find_peaks(
        smoothed,
        height=top * min_peak_ratio,
        distance=max(2, int(round(min_separation_s * fps))),
        prominence=top * 0.20,
    )
    if len(peaks) == 0:
        peaks = np.array([int(np.argmax(smoothed))])

    events = [
        ShotEvent(
            contact_frame=int(p),
            contact_time_s=float(p) / fps,
            peak_speed=float(smoothed[p]),
            wrist=(which[p] or "unknown"),
        )
        for p in sorted(int(p) for p in peaks)
    ]
    return ShotTimingResult(events=events, speed=smoothed, fps=fps)
