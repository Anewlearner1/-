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


def _racket_hand_speed(seq: PlayerLandmarkSequence, hand: str) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame speed of one fixed wrist; undetected frames read as 0.0."""
    idx = L_WRIST if hand == "left" else R_WRIST
    speed = np.nan_to_num(_wrist_speed(seq.landmarks, idx, seq.fps), nan=0.0)
    which = np.full(speed.shape, hand, dtype=object)
    return speed, which


def infer_racket_hand(seq: PlayerLandmarkSequence) -> str:
    """The wrist that reaches the higher *peak* speed over the clip.

    Peak, not total path length: the off hand can travel as far as the racket
    hand over a clip (a serve toss, balancing) while never matching its peak.
    This is the rule the sibling tennis-form-coach project settled on after
    path length picked the wrong hand on real serve footage. Still a guess in
    2D pixels: a clip whose best motion is the off hand will be mislabelled,
    so pass ``hand`` explicitly when you know it.
    """
    peaks = {}
    for name, idx in (("left", L_WRIST), ("right", R_WRIST)):
        v = np.nan_to_num(_wrist_speed(seq.landmarks, idx, seq.fps), nan=0.0)
        if v.size >= 5:
            v = savgol_filter(v, window_length=5, polyorder=2)
        peaks[name] = float(v.max()) if v.size else 0.0
    return "left" if peaks["left"] > peaks["right"] else "right"


MERGE_KEEP_POLICIES = ("earlier", "later", "stronger")


def merge_events(events: list[ShotEvent], fps: float, within_s: float, *,
                 keep: str = "earlier") -> list[ShotEvent]:
    """Collapse events closer than ``within_s`` into one event per group.

    Groups are formed left to right: a group starts at an event and takes in
    every following event at most ``within_s`` after that *first* event. The
    grouping does not depend on ``keep``, so the policies differ only in which
    member survives: the first (``"earlier"``), the last (``"later"``) or the
    one with the highest ``peak_speed`` (``"stronger"``; ties go to the
    earlier). With ``"earlier"`` this is exactly the original behaviour
    (an event is dropped when it is within the window of the last kept one).
    """
    if within_s <= 0:
        raise ValueError("within_s must be positive")
    if keep not in MERGE_KEEP_POLICIES:
        raise ValueError(f"keep must be one of {MERGE_KEEP_POLICIES}, got {keep!r}")
    ordered = sorted(events, key=lambda e: e.contact_frame)
    groups: list[list[ShotEvent]] = []
    for e in ordered:
        if groups and e.contact_frame - groups[-1][0].contact_frame <= within_s * fps:
            groups[-1].append(e)
        else:
            groups.append([e])
    if keep == "earlier":
        return [g[0] for g in groups]
    if keep == "later":
        return [g[-1] for g in groups]
    return [max(g, key=lambda e: e.peak_speed) for g in groups]  # max keeps first on ties


def detect_shots(
    seq: PlayerLandmarkSequence,
    *,
    min_peak_ratio: float = 0.35,
    min_separation_s: float = 0.35,
    smooth_window_s: float = 0.15,
    hand: str | None = None,
    merge_within_s: float | None = None,
    merge_keep: str = "earlier",
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

        hand: ``None`` (default) keeps the original behaviour: the faster of
            the two wrists each frame. ``"left"`` / ``"right"`` follow only
            that wrist, and ``"auto"`` picks one with ``infer_racket_hand``.
            Following one wrist stops the off hand's own swings (it moves a
            lot in a forehand) from registering as shots.

        merge_within_s: if set, events this close together are merged into one
            (``merge_keep`` picks the survivor; by default the earlier). One swing often produces a
            second peak during its follow-through; contact comes first, so the
            earlier event is the one to keep. Off by default: on labelled
            footage the follow-through duplicates sat 10-19 frames after the
            hit while two genuine hits were as close as 20 frames apart, so a
            window long enough to catch every duplicate also merges real hits
            (see docs/real-footage-findings.md). 0.5 s caught 3 of 4 duplicates
            there without merging any hit.

        merge_keep: which event of a merged group survives (see
            ``merge_events``): ``"earlier"`` (default), ``"later"`` or
            ``"stronger"`` (higher smoothed peak speed). Ignored when
            ``merge_within_s`` is None. "earlier" suits the forehand clips the
            window was chosen on; on the Ruud backhand clip the earlier event of
            each duplicate pair was the takeback and the hit came 13-15 frames
            later (docs/real-footage-findings.md).

    Returns:
        ShotTimingResult with one ShotEvent per detected contact, ordered by
        frame, plus the smoothed speed signal they were found on.
    """
    if merge_within_s is not None and merge_within_s <= 0:
        raise ValueError("merge_within_s must be positive")
    if merge_keep not in MERGE_KEEP_POLICIES:
        raise ValueError(f"merge_keep must be one of {MERGE_KEEP_POLICIES}, got {merge_keep!r}")
    if hand not in (None, "auto", "left", "right"):
        raise ValueError(f"hand must be None, 'auto', 'left' or 'right', got {hand!r}")
    fps = seq.fps
    n = seq.frame_count
    if n < 5:
        return ShotTimingResult(events=[], speed=np.zeros(n), fps=fps)

    if hand is None:
        speed, which = combined_wrist_speed(seq)
    else:
        speed, which = _racket_hand_speed(
            seq, infer_racket_hand(seq) if hand == "auto" else hand)

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
    if merge_within_s is not None:
        events = merge_events(events, fps, merge_within_s, keep=merge_keep)
    return ShotTimingResult(events=events, speed=smoothed, fps=fps)
