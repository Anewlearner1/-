"""Split a clip into individual swings and label the phases of each one.

The racket-hand wrist speed profile is the backbone of the segmentation: each
swing shows up as a sharp, isolated speed peak whose apex is, to within a frame
or two, the moment of ball contact.
"""
from __future__ import annotations

import dataclasses

import numpy as np
from scipy.signal import find_peaks

from . import geometry as geo
from .features import Features

PHASE_ORDER = ["ready", "backswing", "forward_swing", "contact",
               "follow_through", "recovery"]

PHASE_LABELS_ZH = {
    "ready": "準備",
    "backswing": "轉身引拍",
    "forward_swing": "向前揮拍",
    "contact": "觸球",
    "follow_through": "隨揮",
    "recovery": "回位",
}


@dataclasses.dataclass
class Swing:
    """One detected swing, in frame indices relative to the whole clip."""

    start: int
    end: int
    contact: int
    takeback: int          # backswing top (racket furthest back)
    turn_start: int        # first meaningful shoulder rotation
    follow_end: int        # swing has decelerated
    peak_speed: float
    swing_dir: np.ndarray  # unit ground vector the racket travels at contact

    @property
    def phases(self) -> dict[str, tuple[int, int]]:
        """Inclusive frame ranges for each phase (may be empty ranges)."""
        return {
            "ready": (self.start, max(self.start, self.turn_start - 1)),
            "backswing": (self.turn_start, self.takeback),
            "forward_swing": (self.takeback, max(self.takeback, self.contact - 1)),
            "contact": (self.contact, self.contact),
            "follow_through": (min(self.contact + 1, self.end), self.follow_end),
            "recovery": (min(self.follow_end + 1, self.end), self.end),
        }

    def phase_at(self, frame: int) -> str | None:
        for name in PHASE_ORDER:
            lo, hi = self.phases[name]
            if lo <= frame <= hi:
                return name
        return None

    def duration(self, fps: float) -> float:
        return (self.end - self.start + 1) / fps


def _ground(v: np.ndarray) -> np.ndarray:
    """Project a 3D vector onto the ground plane (drop the vertical axis)."""
    return np.array([v[0], 0.0, v[2]])


def active_region(speed: np.ndarray, frame: int, floor: float,
                  min_quiet: int) -> tuple[int, int]:
    """Bounds of the contiguous burst of motion containing ``frame``.

    A swing is not one continuous acceleration: the racket almost stops at the
    top of the backswing, so the speed trace shows two humps separated by a
    brief lull. Quiet stretches shorter than ``min_quiet`` frames are therefore
    treated as part of the same swing, while a genuine pause between swings
    ends the region.
    """
    n = len(speed)
    frame = int(np.clip(frame, 0, n - 1))

    def scan(step: int) -> int:
        edge = frame
        i = frame
        quiet = 0
        while 0 <= i + step < n:
            i += step
            if speed[i] > floor:
                edge = i
                quiet = 0
            else:
                quiet += 1
                if quiet >= min_quiet:
                    break
        return edge

    return scan(-1), scan(1)


def _swing_direction(wrist: np.ndarray, contact: int, start: int,
                     window: int = 4) -> np.ndarray:
    """Unit ground-plane direction the racket hand is travelling into contact.

    Built from frames strictly *before* contact only. A window straddling
    contact (some frames before, some after) gets contaminated by the
    follow-through: on a fast stroke the hand is already curling up and across
    the body just 2-3 frames after contact, which points the averaged
    direction toward that wrap-around instead of the direction the ball was
    actually struck. Everything downstream that depends on "which way is
    forward" — contact_in_front, weight_transfer, and takeback detection —
    inherits that error, systematically reading contact as behind the body
    even on clean, well-formed strokes.
    """
    lo = max(start, contact - window)
    hi = contact
    if hi <= lo:
        return np.array([1.0, 0.0, 0.0])
    d = _ground(wrist[hi] - wrist[lo])
    n = np.linalg.norm(d)
    return d / n if n > geo.EPS else np.array([1.0, 0.0, 0.0])


def find_swings(feats: Features, *, min_peak_ratio: float = 0.35,
                min_separation_s: float = 0.5,
                boundary_ratio: float = 0.15,
                floor_ratio: float = 0.05,
                min_quiet_s: float = 0.30,
                min_backswing_travel: float = 0.15,
                max_prep_s: float = 1.5,
                diagnostics: dict | None = None) -> list[Swing]:
    """Detect every swing in the clip.

    Args:
        min_peak_ratio: a peak must reach this fraction of the fastest peak.
        min_separation_s: minimum time between two contacts.
        boundary_ratio: speed threshold (as a fraction of the peak) marking
            where the forward swing has decelerated — the follow-through end.
        floor_ratio: much lower threshold marking true rest, used for the
            swing's outer boundaries. A backswing is far slower than the
            forward swing, so anything higher than this clips the preparation
            phase off the front of the swing and makes the preparation metrics
            unmeasurable.
        min_quiet_s: how long the racket must stay near rest before the swing
            is considered over. Must exceed the natural lull at the top of the
            backswing, or the preparation phase is again lost.
        min_backswing_travel: how far (in torso lengths) the racket must travel
            *against* the eventual swing direction for the motion to count as a
            swing. Recovering to the ready position is a single fast monotonic
            move with no such preparation, and would otherwise be scored as a
            weak swing — which badly skews the summary when the real swings are
            themselves slow.
        max_prep_s: hard cap, in seconds before contact, on how far back the
            preparation window can reach. In continuous rally footage the
            racket hand rarely drops all the way to true rest between
            strokes — there is always some running, adjusting, or recovery
            motion holding speed just above `floor_ratio` — so the active-region
            walk-back can extend `start` well before the actual backswing,
            swallowing the previous shot's follow-through or the footwork
            between points. Any metric keyed off `start`/`turn_start` (knee
            flexion peak, shoulder turn, X-factor) then measures that unrelated
            movement instead of the stroke. A generous real-world prep time
            bounds the damage without needing rally footage to look like the
            clean single-swing case.
        diagnostics: if given a dict, it is filled in with `n_candidates` and
            `n_dropped` (candidates rejected by `min_backswing_travel`) so a
            caller can tell the user when swings were silently excluded,
            instead of the count just disappearing.

    Returns:
        Swings ordered by contact frame; empty if no swing-like motion exists.
    """
    speed = geo.smooth(feats["wrist_speed"], window=5, poly=2)
    n = len(speed)
    if n < 5:
        return []

    top = float(speed.max())
    if top <= geo.EPS:
        return []

    peaks, _ = find_peaks(
        speed,
        height=top * min_peak_ratio,
        distance=max(2, int(min_separation_s * feats.fps)),
        prominence=top * 0.20,
    )
    if len(peaks) == 0:
        peaks = np.array([int(np.argmax(speed))])

    shoulder_yaw = feats["shoulder_yaw"]
    wrist = feats.points["wrist"]
    min_quiet = max(2, int(round(min_quiet_s * feats.fps)))

    swings: list[tuple[float, Swing]] = []
    for k, contact in enumerate(peaks):
        contact = int(contact)
        peak_speed = float(speed[contact])
        floor = peak_speed * floor_ratio
        cutoff = peak_speed * boundary_ratio

        # Outer boundaries: the burst of motion around this peak, clipped so it
        # never crosses into a neighbouring swing.
        #
        # The lower bound is the *previous swing's own follow-through end*,
        # not its contact frame. In fast rally footage two contacts can be
        # under a second apart, and using the previous contact as the cutoff
        # lets this swing's "preparation window" reach back into the previous
        # stroke's still-ongoing follow-through/recovery -- every prep-phase
        # metric (shoulder_turn, knee_flexion_peak, x_factor_peak) then
        # measures the wrong stroke instead of this one. Swings are built in
        # increasing contact order, so the previous swing is already fully
        # resolved and its real follow_end is available here.
        prev_bound = swings[-1][1].follow_end + 1 if swings else 0
        # The upper bound is the quiet valley between this contact and the
        # next one (the true gap between strokes), not the next contact
        # minus one -- the latter can chop this swing's follow-through off
        # mid-motion purely because the next ball arrived soon, which biases
        # finish_height/follow_duration low for otherwise normal swings.
        if k + 1 < len(peaks):
            next_peak = int(peaks[k + 1])
            next_bound = contact + int(np.argmin(speed[contact:next_peak + 1]))
        else:
            next_bound = n - 1
        start, end = active_region(speed, contact, floor, min_quiet)
        start = max(start, prev_bound, contact - int(round(max_prep_s * feats.fps)))
        end = min(end, next_bound)

        # Deceleration point: first frame past contact back under the cutoff.
        follow_end = contact
        while follow_end < end and speed[follow_end + 1] > cutoff:
            follow_end += 1

        swing_dir = _swing_direction(wrist, contact, start)

        # Backswing top = wrist furthest back along the eventual swing path.
        window = slice(start, contact + 1)
        proj = wrist[window] @ swing_dir
        takeback = start + int(np.argmin(proj)) if proj.size else start
        backswing_travel = float(proj[0] - proj.min()) if proj.size else 0.0

        # Unit turn = first frame where the shoulders have rotated meaningfully.
        turn_start = start
        if takeback > start:
            rotation = np.abs(shoulder_yaw[start:takeback + 1] - shoulder_yaw[start])
            moved = np.flatnonzero(rotation > 15.0)
            if moved.size:
                turn_start = start + int(moved[0])

        swings.append((backswing_travel, Swing(
            start=start, end=end, contact=contact,
            takeback=max(takeback, turn_start),
            turn_start=turn_start, follow_end=follow_end,
            peak_speed=peak_speed, swing_dir=swing_dir,
        )))

    kept = [s for travel, s in swings if travel >= min_backswing_travel]
    n_dropped = len(swings) - len(kept)
    # Safety valve: if the clip starts mid-backswing every candidate looks like
    # a recovery. Scoring something beats scoring nothing, so keep them all.
    if not kept:
        kept = [s for _, s in swings]
        n_dropped = 0

    if diagnostics is not None:
        diagnostics["n_candidates"] = len(swings)
        diagnostics["n_dropped"] = n_dropped

    kept.sort(key=lambda s: s.contact)
    return kept
