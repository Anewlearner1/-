"""Target-player selection: a clearly-separated, swappable pipeline stage.

Why this exists as its own module
----------------------------------
The technical plan (docs/technical-plan.md, table in §3) calls for "player
detection + tracking" to lock onto a single target player across the whole
clip, feeding pose estimation. The real version of that stage is an object
detector (a permissively-licensed one -- NOT YOLOv8/Ultralytics, which is
AGPL-3.0; that would need legal sign-off before shipping in a SaaS product,
see docs/technical-plan.md §4) plus a tracker such as ByteTrack, run once per
video to decide *which* bounding box is "the player" and follow it frame to
frame.

That is out of scope for M1 (no GPU in this sandbox, and a real detector is
its own milestone of work). What matters for M1 is that pose estimation
never has to know how the target was chosen. So this module defines:

  * ``PersonCandidate`` -- one detected person in one frame, independent of
    whatever produced it (MediaPipe multi-pose output today, a YOLO+ByteTrack
    box tomorrow).
  * ``PlayerSelector`` -- the interface: given this frame's candidates (and
    optionally the previous frame's chosen candidate, for continuity), return
    the index of the one to treat as "the player", or None if nobody
    qualifies.
  * ``LargestCentralPlayerSelector`` -- today's STUB implementation. It has
    no notion of identity or motion; it just picks whoever looks most like
    "the subject of the shot" in each frame, with light continuity so it
    doesn't flicker between two similarly-sized people. This is a
    placeholder, not a tracker -- see its docstring for exactly what it does
    and does not guarantee.

Swapping in real detection + tracking later means writing a new
``PlayerSelector`` (e.g. backed by ByteTrack track IDs) and passing it into
``pose_overlay.generate_pose_overlay`` -- no change needed to the pose or
rendering code.

Multi-person note
------------------
Unlike tennis-form-coach (a single-player MediaPipe CLI tool that assumes
there is only ever one person in frame), Rally AI's real input may contain
an opponent, ball kids, or bystanders. This module -- and MediaPipe's
``num_poses`` > 1 mode used in ``pose_overlay`` -- exists specifically
because that assumption does not hold here.
"""
from __future__ import annotations

import dataclasses
from typing import Optional, Protocol, Sequence

import numpy as np


@dataclasses.dataclass(frozen=True)
class PersonCandidate:
    """One detected person in one frame, in pixel coordinates.

    Deliberately source-agnostic: today it's built from MediaPipe's
    per-pose landmarks, but the same shape could be built from a YOLO-style
    detector's boxes (with ``landmarks`` left as an empty array) so a future
    ``PlayerSelector`` doesn't care which stage produced it.

    Attributes:
        bbox: (x_min, y_min, x_max, y_max) in pixels.
        landmarks: (N, 2) pixel-space keypoints if available, else shape (0, 2).
        mean_visibility: mean landmark-visibility/confidence in [0, 1], used
            as a crude proxy for "how confidently was this even a person".
    """

    bbox: tuple[float, float, float, float]
    landmarks: np.ndarray
    mean_visibility: float

    @property
    def area(self) -> float:
        x_min, y_min, x_max, y_max = self.bbox
        return max(0.0, x_max - x_min) * max(0.0, y_max - y_min)

    @property
    def centroid(self) -> tuple[float, float]:
        x_min, y_min, x_max, y_max = self.bbox
        return ((x_min + x_max) / 2.0, (y_min + y_max) / 2.0)


class PlayerSelector(Protocol):
    """Interface every player-selection stage must satisfy.

    A real implementation is expected to be stateful (it needs to remember
    who it locked onto) -- ``select`` is called once per frame, in order.
    """

    def select(self, candidates: Sequence[PersonCandidate]) -> Optional[int]:
        """Return the index into ``candidates`` to treat as the target player.

        Returns None if no candidate is an acceptable target for this frame
        (e.g. the list is empty).
        """
        ...

    def reset(self) -> None:
        """Clear any per-clip state (call between videos)."""
        ...


class LargestCentralPlayerSelector:
    """STUB player selector -- NOT real detection or tracking.

    This is the placeholder the plan calls for ("assume single-person
    footage for now" / "use the largest-or-most-central detected person").
    It picks, per frame, the candidate that scores highest on:

      score = bbox_area * frame-centrality_weight

    where centrality weight favours boxes whose centroid is close to the
    frame centre (tennis footage is typically framed with the rallying
    player near the middle). To avoid flickering between two people of
    similar size within a single clip, if a player was already selected on
    a previous frame it is given a continuity bonus for being the candidate
    whose centroid is nearest the previous selection's centroid.

    What this does NOT do (by design -- these are exactly what a real
    ByteTrack-style tracker adds later):
      * No identity/appearance modelling -- it cannot tell two players apart
        if they swap positions or cross paths.
      * No occlusion handling beyond "closest centroid wins that frame".
      * No commitment across a dropped-detection gap longer than one frame;
        if the previously-selected player disappears, it silently
        re-anchors on whoever scores highest that frame.

    It is adequate for single-player clips (M1's stated scope) and for
    clips where the target player is consistently the largest/most central
    figure, but it is a stub, not a tracker, and should be replaced before
    relying on it for multi-player footage.
    """

    def __init__(self, centrality_weight: float = 0.5):
        """
        Args:
            centrality_weight: how strongly to prefer frame-central boxes,
                in [0, 1]. 0 = pure "largest box wins"; 1 = centrality
                dominates area.
        """
        self.centrality_weight = centrality_weight
        self._last_centroid: Optional[tuple[float, float]] = None

    def reset(self) -> None:
        self._last_centroid = None

    def select(self, candidates: Sequence[PersonCandidate]) -> Optional[int]:
        if not candidates:
            return None
        if len(candidates) == 1:
            self._last_centroid = candidates[0].centroid
            return 0

        areas = np.array([c.area for c in candidates], dtype=float)
        max_area = areas.max() or 1.0
        norm_area = areas / max_area

        centroids = np.array([c.centroid for c in candidates], dtype=float)

        if self._last_centroid is not None:
            # Continuity: prefer whoever is spatially closest to the last pick.
            prev = np.array(self._last_centroid, dtype=float)
            dist = np.linalg.norm(centroids - prev, axis=1)
            max_dist = dist.max() or 1.0
            continuity = 1.0 - (dist / max_dist)
            score = (1 - self.centrality_weight) * norm_area + self.centrality_weight * continuity
        else:
            # No history yet: fall back to frame-centrality using the mean
            # of all candidate centroids as a proxy for "frame centre".
            frame_centre = centroids.mean(axis=0)
            dist = np.linalg.norm(centroids - frame_centre, axis=1)
            max_dist = dist.max() or 1.0
            centrality = 1.0 - (dist / max_dist)
            score = (1 - self.centrality_weight) * norm_area + self.centrality_weight * centrality

        best = int(np.argmax(score))
        self._last_centroid = candidates[best].centroid
        return best


def default_player_selector() -> PlayerSelector:
    """The player-selection stage used by pose_overlay today.

    Swap this for a real detector+tracker-backed ``PlayerSelector`` when one
    is available -- everything downstream only depends on the ``PlayerSelector``
    protocol, not on this stub.
    """
    return LargestCentralPlayerSelector()
