"""Racket speed, ball launch speed and launch angle.

A different question from the scoring rubric's, and a different kind of
answer: the rubric asks "is this good technique?" and grades against reference
bands, while this asks "how fast, and at what angle?" and reports measured
physical quantities. They share the front half of the pipeline (pose ->
features -> swing segmentation) and nothing after it.

Three numbers, in decreasing order of how much you should trust them:

1. **Racket head speed** -- derived from pose alone, no ball needed. The most
   reliable of the three, because MediaPipe tracks the arm well and the only
   modelling step is extending the forearm by a racket length.

2. **Ball launch speed** -- needs the ball found in several frames after
   contact, plus a pixels-to-metres scale. Reported with the number of frames
   it was actually measured over, because that is what determines whether to
   believe it.

3. **Launch angle** -- same tracking as (2), and additionally only meaningful
   from a side-on camera, since it is measured in the image plane.

Every function here returns None rather than a guess when its inputs aren't
good enough, and `measure_swing` reports which ones came back empty.
"""
from __future__ import annotations

import dataclasses

import numpy as np

from . import ball as ball_mod
from . import geometry as geo
from . import pose as P
from .features import Features
from .segment import Swing

#: Distance from the wrist joint to the centre of the string bed, in metres.
#: A 27in (68.6cm) racket gripped near the butt cap puts the middle of the
#: string bed roughly 45cm past the wrist; the exact figure varies with grip
#: and racket, and it scales the reported racket speed linearly, so it is
#: exposed as a parameter rather than buried.
RACKET_LENGTH_M = 0.45

#: How many frames after contact to try to follow the ball for. Long enough to
#: fit a direction through, short enough that the constant-depth assumption
#: behind the pixel scale still roughly holds (the ball is moving away from
#: the camera the whole time).
BALL_TRACK_FRAMES = 8

#: Fewest ball positions needed before a speed/angle is reported at all.
MIN_BALL_POINTS = 3


#: How far the fastest moment of the swing may sit from the detected contact
#: frame before it is worth flagging, in seconds. The racket head peaks a
#: frame or two off the wrist by nature (the forearm is still rotating), so a
#: small offset is normal; a large one means the contact frame is wrong.
CONTACT_PEAK_TOLERANCE_S = 0.12


@dataclasses.dataclass
class SwingKinetics:
    """Measured physical quantities for one swing."""

    contact_frame: int
    racket_speed_kmh: float | None   # peak racket-head speed within the swing
    racket_peak_frame: int
    wrist_speed_kmh: float | None    # peak racket-hand wrist speed
    ball_speed_kmh: float | None
    launch_angle_deg: float | None
    ball_points: int                 # how many frames the ball was found in
    notes: list[str]

    def to_dict(self) -> dict:
        return {
            "contact_frame": self.contact_frame,
            "racket_speed_kmh": _round(self.racket_speed_kmh),
            "racket_peak_frame": self.racket_peak_frame,
            "wrist_speed_kmh": _round(self.wrist_speed_kmh),
            "ball_speed_kmh": _round(self.ball_speed_kmh),
            "launch_angle_deg": _round(self.launch_angle_deg),
            "ball_points": self.ball_points,
            "notes": self.notes,
        }


def _round(x: float | None) -> float | None:
    return None if x is None else round(float(x), 1)


# --------------------------------------------------------------- racket speed
def racket_head_track(feats: Features) -> np.ndarray:
    """A virtual racket-head trajectory, in torso lengths.

    The racket is assumed to extend the forearm: head = wrist + L * unit(wrist
    - elbow). That captures both of the things that make the head faster than
    the wrist -- the hand's own travel, and the racket swinging about the wrist
    -- without needing to see the racket, which MediaPipe cannot do.

    It is an approximation, and it is worth knowing which way it errs: the
    racket is only truly in line with the forearm at full extension. Through a
    forehand's wrist lay-back or a serve's pronation the real racket points
    somewhat off the forearm axis, and this model then understates the head's
    speed.
    """
    wrist = feats.points["wrist"]
    elbow = feats.points["elbow"]
    reach = RACKET_LENGTH_M / feats.scale          # racket length in torso units
    return wrist + reach * geo.unit(wrist - elbow)


def _peak_within(series_torso: np.ndarray, feats: Features,
                 swing: Swing) -> tuple[float | None, int]:
    """Peak of a torso-units/second series inside the swing, as km/h + frame."""
    lo = max(0, swing.takeback)
    hi = min(len(series_torso) - 1, swing.follow_end)
    if hi <= lo:
        return None, swing.contact
    idx = lo + int(np.argmax(series_torso[lo:hi + 1]))
    return float(series_torso[idx]) * feats.scale * 3.6, idx


def peak_racket_speed(feats: Features, swing: Swing) -> tuple[float | None, int]:
    """Fastest racket-head speed within the swing, and the frame it happens on.

    This is what "swing speed" normally means -- the peak the racket head
    reaches driving through the ball, which is the figure quoted for players
    and the one that actually bounds how hard the ball can leave.
    """
    head = racket_head_track(feats)
    speed_torso = geo.smooth(geo.speed(head, feats.fps), window=5, poly=2)
    return _peak_within(speed_torso, feats, swing)


def peak_wrist_speed(feats: Features, swing: Swing) -> tuple[float | None, int]:
    """Fastest racket-hand wrist speed within the swing, as km/h + frame.

    Reported alongside the racket-head figure because it is measured straight
    from a tracked landmark, with no racket model in between -- if the two
    disagree wildly, it is the racket model to doubt first.
    """
    speed_torso = geo.smooth(feats["wrist_speed"], window=5, poly=2)
    return _peak_within(speed_torso, feats, swing)


# ------------------------------------------------------------------ ball unit
def metres_per_pixel(seq: P.PoseSequence, feats: Features, frame: int) -> float | None:
    """Real-world size of one pixel, at the player's distance from the camera.

    Derived from the player themselves: their torso is a known number of
    metres (`feats.scale`) and a measurable number of pixels, so the ratio
    gives a scale without needing court lines or any calibration step.

    The catch is that it is only valid at the player's depth. A ball flying
    away from the camera shrinks, so a fixed scale increasingly *under*-states
    the distance it covered -- which is why the ball is only followed for a
    handful of frames and why the resulting speed should be read as a floor,
    not an exact figure.
    """
    if not (0 <= frame < seq.n_frames):
        return None
    shoulder_mid = (seq.pixel[frame, P.L_SHOULDER] + seq.pixel[frame, P.R_SHOULDER]) / 2.0
    hip_mid = (seq.pixel[frame, P.L_HIP] + seq.pixel[frame, P.R_HIP]) / 2.0
    torso_px = float(np.linalg.norm(shoulder_mid - hip_mid))
    if torso_px < 1.0:
        return None
    return feats.scale / torso_px


def ball_launch(seq: P.PoseSequence, feats: Features, video, contact: int
                ) -> tuple[float | None, float | None, int, list[str]]:
    """Ball speed (km/h) and launch angle (degrees) just after contact.

    Returns (speed, angle, n_points, notes). Speed and angle are None when the
    ball could not be followed for `MIN_BALL_POINTS` frames.

    The angle is the elevation of the ball's path in the *image plane*:
    positive is upward, zero is horizontal across the frame. From a side-on
    camera that is the launch angle; from behind or in front it is not, and
    the caller is expected to know which it has.
    """
    notes: list[str] = []
    scale = metres_per_pixel(seq, feats, contact)
    if scale is None:
        return None, None, 0, ["無法從球員身形推算像素比例尺"]

    racket_head_px = _racket_head_pixel(seq, feats, contact)
    hi = min(contact + BALL_TRACK_FRAMES, seq.n_frames - 1)
    track = ball_mod.track_from(video, contact, hi, seed_xy=racket_head_px,
                                seed_radius=_seed_radius_px(seq, contact))

    if len(track) < MIN_BALL_POINTS:
        notes.append(f"只在 {len(track)} 幀找到球（需要 {MIN_BALL_POINTS} 幀以上）"
                     "，無法計算球速與角度")
        return None, None, len(track), notes

    frames = np.array([f for f, _, _ in track], float)
    xs = np.array([x for _, x, _ in track], float)
    ys = np.array([y for _, _, y in track], float)

    # Straight-line fit through the tracked points. Over this many frames the
    # ball's drop is small compared with its travel, and a line is far more
    # robust to a single mis-detected blob than differencing adjacent frames.
    vx = float(np.polyfit(frames, xs, 1)[0])      # pixels per frame
    vy = float(np.polyfit(frames, ys, 1)[0])

    speed_px_per_frame = float(np.hypot(vx, vy))
    speed_kmh = speed_px_per_frame * feats.fps * scale * 3.6

    # Image y grows downward, so an upward path has negative vy.
    angle_deg = float(np.degrees(np.arctan2(-vy, abs(vx)))) if abs(vx) > geo.EPS else None
    if angle_deg is None:
        notes.append("球幾乎沒有水平位移，無法判斷擊球角度")

    return speed_kmh, angle_deg, len(track), notes


def _racket_head_pixel(seq: P.PoseSequence, feats: Features, frame: int) -> tuple[float, float]:
    """Where the racket head is, in pixels -- the first place to look for the ball."""
    wr = P.R_WRIST if feats.hand == "right" else P.L_WRIST
    el = P.R_ELBOW if feats.hand == "right" else P.L_ELBOW
    wrist = seq.pixel[frame, wr]
    elbow = seq.pixel[frame, el]
    scale = metres_per_pixel(seq, feats, frame)
    reach_px = (RACKET_LENGTH_M / scale) if scale else 0.0
    direction = wrist - elbow
    norm = float(np.linalg.norm(direction))
    if norm < geo.EPS:
        return float(wrist[0]), float(wrist[1])
    head = wrist + reach_px * direction / norm
    return float(head[0]), float(head[1])


def _seed_radius_px(seq: P.PoseSequence, frame: int) -> float:
    """How far from the racket head to look for the ball on the first frame."""
    shoulder_mid = (seq.pixel[frame, P.L_SHOULDER] + seq.pixel[frame, P.R_SHOULDER]) / 2.0
    hip_mid = (seq.pixel[frame, P.L_HIP] + seq.pixel[frame, P.R_HIP]) / 2.0
    torso_px = float(np.linalg.norm(shoulder_mid - hip_mid))
    return max(40.0, torso_px * 1.2)


# --------------------------------------------------------------------- public
def measure_swing(seq: P.PoseSequence, feats: Features, swing: Swing, video
                  ) -> SwingKinetics:
    """Measure racket speed, ball speed and launch angle for one swing."""
    notes: list[str] = []
    contact = swing.contact

    racket_kmh, peak_frame = peak_racket_speed(feats, swing)
    wrist_kmh, _ = peak_wrist_speed(feats, swing)

    # The fastest moment of the swing should be at contact, give or take the
    # frame or two the racket head naturally lags the wrist by. A big gap
    # means the contact frame is on the wrong part of the motion, and every
    # ball measurement below inherits that error.
    gap_s = abs(peak_frame - contact) / feats.fps
    if gap_s > CONTACT_PEAK_TOLERANCE_S:
        notes.append(
            f"揮拍最快的瞬間在第 {peak_frame} 幀，距離判定的觸球幀 {contact} "
            f"有 {gap_s:.2f} 秒，觸球幀可能抓錯，球速與角度請對照畫面確認")

    ball_kmh, angle, n_points, ball_notes = ball_launch(seq, feats, video, contact)
    notes.extend(ball_notes)

    return SwingKinetics(
        contact_frame=contact,
        racket_speed_kmh=racket_kmh,
        racket_peak_frame=peak_frame,
        wrist_speed_kmh=wrist_kmh,
        ball_speed_kmh=ball_kmh,
        launch_angle_deg=angle,
        ball_points=n_points,
        notes=notes,
    )
