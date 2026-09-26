"""Decide which stroke a detected swing is, so the right rubric gets applied."""
from __future__ import annotations

from .features import Features
from .segment import Swing

#: Contact this far above the head (torso lengths) can only be a serve/overhead
#: -- no groundstroke lifts the racket-hand wrist this high.
SERVE_HEIGHT_THRESHOLD = 0.3


def classify_stroke(feats: Features, swing: Swing) -> str:
    """"forehand" | "backhand" | "serve"."""
    if feats["wrist_above_head"][swing.contact] > SERVE_HEIGHT_THRESHOLD:
        return "serve"
    # wrist_lateral is already signed positive on the racket-hand side (see
    # features.py), so this comparison doesn't need to know which hand it is:
    # racket taken back on the strong side -> forehand; crossed over ->
    # backhand. Read at `takeback` (top of the backswing), not `turn_start`:
    # at turn_start the racket has barely started moving off the body, so the
    # lateral signal hasn't developed yet and is dominated by noise.
    return "forehand" if feats["wrist_lateral"][swing.takeback] >= 0 else "backhand"
