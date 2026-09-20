"""Tests for the speed / launch-angle measurements.

The racket-speed half is checked against hand-computable ground truth on a
synthetic pose sequence. The ball half needs a real video file (cv2 seeks by
frame index, which an array can't stand in for), so it gets its own rendered
clip with a ball moving at a known pixel velocity -- which, combined with a
known torso size in pixels, gives a known real-world speed to assert against.
"""
import numpy as np
import pytest

from synth import TORSO, synth_swing_sequence
from tennis_coach import kinetics, pose as P
from tennis_coach.features import extract_features
from tennis_coach.segment import find_swings

cv2 = pytest.importorskip("cv2")


@pytest.fixture(scope="module")
def swing():
    seq = synth_swing_sequence(n_swings=1)
    feats = extract_features(seq)
    swings = find_swings(feats)
    assert swings, "夾具前提：合成揮拍必須被偵測到"
    return seq, feats, swings[0]


# ------------------------------------------------------------- racket model
def test_the_virtual_racket_head_sits_a_racket_length_past_the_wrist(swing):
    _, feats, _ = swing
    head = kinetics.racket_head_track(feats)
    wrist = feats.points["wrist"]
    gap_torso = np.linalg.norm(head - wrist, axis=-1)
    gap_m = gap_torso * feats.scale
    assert np.allclose(gap_m, kinetics.RACKET_LENGTH_M, atol=1e-6)


def test_the_racket_head_extends_the_forearm_not_some_other_direction(swing):
    _, feats, _ = swing
    head = kinetics.racket_head_track(feats)
    wrist, elbow = feats.points["wrist"], feats.points["elbow"]
    forearm = wrist - elbow
    extension = head - wrist
    cos = np.sum(forearm * extension, axis=-1) / (
        np.linalg.norm(forearm, axis=-1) * np.linalg.norm(extension, axis=-1))
    assert np.allclose(cos, 1.0, atol=1e-6), "拍頭必須沿著前臂方向延伸"


def test_the_racket_head_outruns_the_wrist(swing):
    """The whole point of modelling the racket: the head travels further per
    unit time than the hand holding it, because it also swings about it."""
    _, feats, sw = swing
    racket, _ = kinetics.peak_racket_speed(feats, sw)
    wrist, _ = kinetics.peak_wrist_speed(feats, sw)
    assert racket > wrist


def test_racket_length_scales_the_reported_speed(swing):
    """It is a modelling constant, not a measurement, so its effect must be
    visible and predictable rather than buried."""
    _, feats, sw = swing
    original = kinetics.RACKET_LENGTH_M
    try:
        kinetics.RACKET_LENGTH_M = 0.45
        base, _ = kinetics.peak_racket_speed(feats, sw)
        kinetics.RACKET_LENGTH_M = 0.90
        doubled, _ = kinetics.peak_racket_speed(feats, sw)
    finally:
        kinetics.RACKET_LENGTH_M = original
    assert doubled > base


def test_speed_is_reported_in_kmh_not_torso_lengths(swing):
    """Guard against the normalised unit leaking out: a swing measured in
    torso lengths per second would read as a plausible-looking small number."""
    _, feats, sw = swing
    from tennis_coach import geometry as geo
    head = kinetics.racket_head_track(feats)
    peak_torso = float(geo.smooth(geo.speed(head, feats.fps), window=5, poly=2)
                       [sw.takeback:sw.follow_end + 1].max())
    reported, _ = kinetics.peak_racket_speed(feats, sw)
    assert reported == pytest.approx(peak_torso * feats.scale * 3.6, rel=1e-6)


# --------------------------------------------------------------- pixel scale
def test_pixel_scale_comes_from_the_players_own_torso(swing):
    seq, feats, sw = swing
    scale = kinetics.metres_per_pixel(seq, feats, sw.contact)
    shoulder = (seq.pixel[sw.contact, P.L_SHOULDER] + seq.pixel[sw.contact, P.R_SHOULDER]) / 2
    hip = (seq.pixel[sw.contact, P.L_HIP] + seq.pixel[sw.contact, P.R_HIP]) / 2
    torso_px = float(np.linalg.norm(shoulder - hip))
    assert scale == pytest.approx(TORSO / torso_px, rel=1e-6)


def test_pixel_scale_refuses_a_frame_outside_the_clip(swing):
    seq, feats, _ = swing
    assert kinetics.metres_per_pixel(seq, feats, seq.n_frames + 5) is None


# ----------------------------------------------------------------- ball half
W, H = 640, 480
COURT = (40, 120, 30)
BALL_BGR = (40, 230, 230)


@pytest.fixture
def ball_video(tmp_path):
    """A clip where the ball moves at exactly (+40, -20) pixels per frame.

    Returns the path plus that known velocity, so a test can work out what
    speed and angle *should* come back.
    """
    path = tmp_path / "ball.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (W, H))
    if not writer.isOpened():
        pytest.skip("此環境缺少 mp4 編碼器")

    vx, vy, n = 40, -20, 12
    x0, y0 = 80, 400
    for i in range(n):
        frame = np.zeros((H, W, 3), np.uint8)
        frame[:] = COURT
        cv2.circle(frame, (x0 + vx * i, y0 + vy * i), 7, BALL_BGR, -1, cv2.LINE_AA)
        writer.write(frame)
    writer.release()
    return path, (x0, y0), (vx, vy), n


def test_the_ball_is_followed_across_frames(ball_video):
    path, (x0, y0), (vx, vy), n = ball_video
    track = kinetics.ball_mod.track_from(path, 0, n - 1, seed_xy=(x0, y0),
                                         seed_radius=40)
    assert len(track) >= kinetics.MIN_BALL_POINTS
    # Positions should follow the straight line the fixture drew.
    for f, x, y in track:
        assert x == pytest.approx(x0 + vx * f, abs=3)
        assert y == pytest.approx(y0 + vy * f, abs=3)


def test_tracking_stops_instead_of_wandering_when_the_ball_leaves(tmp_path):
    """The ball vanishes after a few frames; the track must end, not drift."""
    path = tmp_path / "vanish.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (W, H))
    if not writer.isOpened():
        pytest.skip("此環境缺少 mp4 編碼器")
    for i in range(10):
        frame = np.zeros((H, W, 3), np.uint8)
        frame[:] = COURT
        if i < 4:
            cv2.circle(frame, (100 + 30 * i, 300), 7, BALL_BGR, -1, cv2.LINE_AA)
        writer.write(frame)
    writer.release()

    track = kinetics.ball_mod.track_from(path, 0, 9, seed_xy=(100, 300), seed_radius=40)
    assert len(track) <= 5, "球消失後不應繼續產生偵測結果"


def test_launch_speed_and_angle_match_the_known_pixel_velocity(ball_video, swing):
    """End-to-end on the ball half: known pixel velocity + known pixel scale
    must come back as the corresponding real-world speed and angle."""
    path, (x0, y0), (vx, vy), n = ball_video
    seq, feats, _ = swing

    # Pin the scale by construction rather than depending on the pose fixture's
    # framing: pretend one pixel is exactly 1 cm.
    metres_per_px = 0.01
    speed, angle, points, notes = _launch_with_fixed_scale(
        kinetics, seq, feats, path, contact=0, seed=(x0, y0),
        metres_per_px=metres_per_px)

    assert points >= kinetics.MIN_BALL_POINTS
    expected_kmh = float(np.hypot(vx, vy)) * feats.fps * metres_per_px * 3.6
    assert speed == pytest.approx(expected_kmh, rel=0.05)
    # Image y grows downward, so (+40, -20) px/frame is upward at ~26.6 deg.
    assert angle == pytest.approx(np.degrees(np.arctan2(20, 40)), abs=2.0)


def test_too_few_ball_frames_reports_nothing_rather_than_guessing(tmp_path, swing):
    path = tmp_path / "empty.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (W, H))
    if not writer.isOpened():
        pytest.skip("此環境缺少 mp4 編碼器")
    for _ in range(8):                       # a court, and no ball at all
        frame = np.zeros((H, W, 3), np.uint8)
        frame[:] = COURT
        writer.write(frame)
    writer.release()

    seq, feats, _ = swing
    speed, angle, points, notes = _launch_with_fixed_scale(
        kinetics, seq, feats, path, contact=0, seed=(100, 100), metres_per_px=0.01)
    assert speed is None and angle is None
    assert points < kinetics.MIN_BALL_POINTS
    assert notes, "量不到就要說明原因，不能靜默回傳 None"


def _launch_with_fixed_scale(mod, seq, feats, video, contact, seed, metres_per_px,
                             monkey=None):
    """Run ball_launch with the pixel scale and seed point pinned.

    The real function derives both from the player's pose in the frame; these
    tests are about the measurement maths downstream of that, so both are
    replaced with known constants.
    """
    real_scale = mod.metres_per_pixel
    real_seed = mod._racket_head_pixel
    real_radius = mod._seed_radius_px
    try:
        mod.metres_per_pixel = lambda *a, **k: metres_per_px
        mod._racket_head_pixel = lambda *a, **k: seed
        mod._seed_radius_px = lambda *a, **k: 40.0
        return mod.ball_launch(seq, feats, video, contact)
    finally:
        mod.metres_per_pixel = real_scale
        mod._racket_head_pixel = real_seed
        mod._seed_radius_px = real_radius


# ---------------------------------------------------------------- integration
def test_measure_swing_fills_in_what_it_can_and_says_what_it_cannot(swing, tmp_path):
    """No video file at all: racket speed still works, ball half degrades."""
    seq, feats, sw = swing
    result = kinetics.measure_swing(seq, feats, sw, tmp_path / "does_not_exist.mp4")

    assert result.racket_speed_kmh is not None
    assert result.wrist_speed_kmh is not None
    assert result.ball_speed_kmh is None
    assert result.launch_angle_deg is None
    assert result.ball_points == 0
    assert result.notes

    as_dict = result.to_dict()
    assert as_dict["racket_speed_kmh"] == pytest.approx(result.racket_speed_kmh, abs=0.05)
    assert as_dict["ball_speed_kmh"] is None
