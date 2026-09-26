"""Per-swing technique metrics derived from features + swing phase boundaries.

Each metric is a single number describing one aspect of technique that the
scoring rubric grades against a reference band (see `scoring.py`). Every value
is read at whichever phase boundary makes physical sense for that metric --
shoulder turn over the backswing, contact height at contact -- rather than
uniformly at one frame.
"""
from __future__ import annotations

import dataclasses

import numpy as np

from . import geometry as geo
from .features import Features
from .segment import Swing


@dataclasses.dataclass
class SwingMetrics:
    prep_time_s: float                  # backswing duration: turn_start -> takeback
    shoulder_turn_deg: float            # shoulder rotation from turn_start to takeback
    hip_shoulder_separation_deg: float  # peak |x_factor| between takeback and contact
    knee_bend_deg: float                # deepest knee flexion during backswing/load
    contact_height: float               # wrist_above_shoulder at contact, torso units
    contact_wrist_speed_kmh: float      # peak wrist speed within +-2 frames of contact
    follow_through_time_s: float        # contact -> follow_end
    stance_width: float                 # stance_width at takeback, torso units
    trunk_lean_deg: float               # trunk_lean at contact
    weight_transfer: float              # peak com_rise_speed, takeback -> contact

    def to_dict(self) -> dict:
        return {k: round(float(v), 4) for k, v in dataclasses.asdict(self).items()}


def _clip(lo: int, hi: int, n: int) -> tuple[int, int]:
    return max(0, min(n - 1, lo)), max(0, min(n - 1, hi))


def extract_metrics(feats: Features, swing: Swing) -> SwingMetrics:
    s = feats.series
    n = feats.n_frames
    fps = feats.fps

    def at(name: str, frame: int) -> float:
        frame = max(0, min(n - 1, frame))
        return float(s[name][frame])

    def peak_abs(name: str, lo: int, hi: int) -> float:
        lo, hi = _clip(lo, hi, n)
        if hi <= lo:
            return abs(at(name, lo))
        window = s[name][lo:hi + 1]
        return float(np.max(np.abs(window)))

    def peak(name: str, lo: int, hi: int) -> float:
        lo, hi = _clip(lo, hi, n)
        if hi <= lo:
            return at(name, lo)
        return float(np.max(s[name][lo:hi + 1]))

    def min_(name: str, lo: int, hi: int) -> float:
        lo, hi = _clip(lo, hi, n)
        if hi <= lo:
            return at(name, lo)
        return float(np.min(s[name][lo:hi + 1]))

    # From true rest (`start`), not `turn_start`: the latter is only the frame
    # where rotation first crosses a noise threshold, which by construction
    # has already turned some amount -- measuring from there undercounts the
    # backswing's actual total rotation.
    shoulder_turn = abs(at("shoulder_yaw", swing.takeback) - at("shoulder_yaw", swing.start))

    # knee_mean is already flexion, not joint angle (0 == straight leg, larger
    # == deeper bend -- see features.py), so the deepest bend is its peak.
    knee_bend = peak("knee_mean", swing.turn_start, max(swing.takeback, swing.contact))

    # Peak wrist speed right around contact, not the whole swing -- this is a
    # sanity/consistency metric for the rubric, not the kinetics pipeline's
    # "how fast overall" figure.
    wrist_speed_smooth = geo.smooth(s["wrist_speed"], window=5, poly=2)
    lo, hi = _clip(swing.contact - 2, swing.contact + 2, n)
    contact_speed_torso = float(np.max(wrist_speed_smooth[lo:hi + 1])) if hi > lo else float(wrist_speed_smooth[lo])

    return SwingMetrics(
        prep_time_s=(swing.takeback - swing.turn_start) / fps,
        shoulder_turn_deg=shoulder_turn,
        hip_shoulder_separation_deg=peak_abs("x_factor", swing.takeback, swing.contact),
        knee_bend_deg=knee_bend,
        contact_height=at("wrist_above_shoulder", swing.contact),
        contact_wrist_speed_kmh=contact_speed_torso * feats.scale * 3.6,
        follow_through_time_s=(swing.follow_end - swing.contact) / fps,
        stance_width=at("stance_width", swing.takeback),
        trunk_lean_deg=at("trunk_lean", swing.contact),
        weight_transfer=_weight_transfer(feats, swing, n),
    )


def _weight_transfer(feats: Features, swing: Swing, n: int) -> float:
    """How far the hips drove forward, into the shot, from takeback to contact.

    Projected onto the swing direction (torso units) rather than measured as
    raw vertical rise: a forward weight shift is the coaching-standard sense
    of "weight transfer", and it is what actually varies with a player
    driving into the ball versus staying passive on their back foot.
    """
    lo, hi = _clip(swing.takeback, swing.contact, n)
    hip = feats.points["hip_mid"]
    return float(np.dot(hip[hi] - hip[lo], swing.swing_dir))
