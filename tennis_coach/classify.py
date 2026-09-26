"""Decide which stroke a detected swing is, so the right rubric gets applied."""
from __future__ import annotations

from .features import Features
from .segment import Swing

#: Contact this far above the head (torso lengths) can only be a serve/overhead
#: -- no groundstroke lifts the racket-hand wrist this high.
SERVE_HEAD_HEIGHT_THRESHOLD = 0.3

#: Contact this far above the HIPS (torso lengths) is the same signal measured
#: against a more stable reference point. `wrist_above_head` depends on the
#: nose landmark alone, which moves with head tilt -- on a real second serve
#: in test footage the player's head angle shifted just enough between the
#: two serves that the same contact height read 0.36 above head on the first
#: serve and only 0.23 (under threshold) on the second, misclassifying a
#: genuine serve as a forehand. hip_mid is the average of two landmarks and
#: does not tilt with the head, so it stays reliable across repeated serves.
SERVE_HIP_HEIGHT_THRESHOLD = 1.3


def classify_stroke(feats: Features, swing: Swing) -> str:
    """"forehand" | "backhand" | "serve"."""
    above_head = feats["wrist_above_head"][swing.contact]
    above_hip = feats["wrist_above_hip"][swing.contact]
    if above_head > SERVE_HEAD_HEIGHT_THRESHOLD or above_hip > SERVE_HIP_HEIGHT_THRESHOLD:
        return "serve"
    # wrist_lateral is already signed positive on the racket-hand side (see
    # features.py), so this comparison doesn't need to know which hand it is:
    # racket taken back on the strong side -> forehand; crossed over ->
    # backhand. Read at `takeback` (top of the backswing), not `turn_start`:
    # at turn_start the racket has barely started moving off the body, so the
    # lateral signal hasn't developed yet and is dominated by noise.
    return "forehand" if feats["wrist_lateral"][swing.takeback] >= 0 else "backhand"
