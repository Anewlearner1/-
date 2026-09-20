"""Finding the ball in the frame.

Two jobs, with different tolerances:

* `find_ball` / `detect_contact_frame` -- locate the ball *near the racket*, to
  confirm when contact happened. Strict filtering: a lucky false positive here
  would silently outrank a decent motion-based estimate, so it is better to
  report nothing.
* `track_from` -- follow the ball *after* it leaves the strings, to measure how
  fast and at what angle. Looser shape filtering (a ball travelling at speed is
  a blurred streak, not a circle) but tight motion gating: each detection has
  to be somewhere the previous one could plausibly have reached.
"""
from __future__ import annotations

import numpy as np

#: Optic-yellow tennis ball in OpenCV HSV (H: 0-179, S/V: 0-255). Wide enough
#: to survive motion blur washing out saturation, narrow enough to reject the
#: green of a hard/grass court and most sponsor-board colours.
BALL_HSV_LO = (25, 60, 80)
BALL_HSV_HI = (48, 255, 255)

MIN_BALL_AREA_PX = 3
MAX_BALL_AREA_PX = 900
#: 1.0 = a perfect circle. Motion blur stretches a ball into an ellipse, so
#: this is deliberately forgiving rather than tight.
MIN_CIRCULARITY = 0.45
#: Looser still while following a ball in flight, where the streak can be
#: several times longer than it is wide.
TRACKING_CIRCULARITY = 0.15

#: Fraction of a search window's frames that must yield a trackable ball
#: before `detect_contact_frame`'s result is trusted at all. Below this, a
#: single lucky frame could decide the answer off noise instead of a real,
#: followable ball.
MIN_COVERAGE = 0.5


def find_ball(frame_bgr: np.ndarray, roi: tuple[int, int, int, int],
              min_circularity: float = MIN_CIRCULARITY) -> tuple[float, float] | None:
    """The largest ball-coloured, ball-shaped blob's centre within ``roi``.

    ``roi`` is (x0, y0, x1, y1) in the frame's own pixel coordinates. Returns
    None if nothing in the region passes both the colour and shape filter.
    """
    import cv2

    x0, y0, x1, y1 = (int(v) for v in roi)
    x0, y0 = max(0, x0), max(0, y0)
    x1 = min(frame_bgr.shape[1], x1)
    y1 = min(frame_bgr.shape[0], y1)
    if x1 <= x0 or y1 <= y0:
        return None

    crop = frame_bgr[y0:y1, x0:x1]
    if crop.size == 0:
        return None

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(BALL_HSV_LO), np.array(BALL_HSV_HI))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best: tuple[float, float] | None = None
    best_area = -1.0
    for c in contours:
        area = cv2.contourArea(c)
        if not (MIN_BALL_AREA_PX <= area <= MAX_BALL_AREA_PX):
            continue
        perimeter = cv2.arcLength(c, True)
        if perimeter <= 0:
            continue
        circularity = 4.0 * np.pi * area / (perimeter ** 2)
        if circularity < min_circularity:
            continue
        if area > best_area:
            (cx, cy), _ = cv2.minEnclosingCircle(c)
            best_area = area
            best = (x0 + cx, y0 + cy)
    return best


def detect_contact_frame(video_path, pixel_wrist: np.ndarray, lo: int, hi: int,
                         reach_px: float) -> int | None:
    """The frame in [lo, hi] where the tracked ball sits closest to the wrist.

    None if the ball wasn't trackable across enough of the window to trust the
    result -- callers should fall back to a motion-based estimate rather than
    act on one lucky (or unlucky) frame.
    """
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None

    positions: dict[int, tuple[float, float]] = {}
    try:
        for f in range(lo, hi + 1):
            if f < 0 or f >= len(pixel_wrist):
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, frame = cap.read()
            if not ok:
                continue
            wx, wy = pixel_wrist[f]
            roi = (wx - reach_px, wy - reach_px, wx + reach_px, wy + reach_px)
            found = find_ball(frame, roi)
            if found is not None:
                positions[f] = found
    finally:
        cap.release()

    n = hi - lo + 1
    if len(positions) < max(2, int(round(n * MIN_COVERAGE))):
        return None

    return min(
        positions,
        key=lambda f: np.hypot(positions[f][0] - pixel_wrist[f][0],
                               positions[f][1] - pixel_wrist[f][1]),
    )


def track_from(video_path, lo: int, hi: int, seed_xy: tuple[float, float],
               seed_radius: float) -> list[tuple[int, float, float]]:
    """Follow the ball from frame ``lo`` to ``hi``, starting near ``seed_xy``.

    Returns [(frame, x, y), ...] for the frames the ball was found in, which
    may be fewer than the range asked for and may stop early.

    The search is motion-gated rather than appearance-gated, because after
    contact the ball is the fastest thing in the frame and its appearance is
    the least reliable thing about it:

    * first frame -- look near the racket head, where the ball must be;
    * second frame -- no velocity estimate yet, so the gate has to be wide
      enough to cover a full frame of travel (a served ball can cross several
      hundred pixels in one frame at 60fps);
    * after that -- predict from the last step and search a modest radius
      around the prediction, widening it each time a frame comes up empty so a
      single miss doesn't end the track.
    """
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []

    track: list[tuple[int, float, float]] = []
    last: tuple[float, float] | None = None
    velocity: tuple[float, float] | None = None
    misses = 0

    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, lo)
        for f in range(lo, hi + 1):
            ok, frame = cap.read()
            if not ok:
                break

            if last is None:
                centre, radius = seed_xy, seed_radius
                circularity = MIN_CIRCULARITY
            elif velocity is None:
                # One point so far: the ball could be anywhere within a frame's
                # worth of travel, and we have no idea which way yet.
                centre, radius = last, max(frame.shape[:2]) * 0.5
                circularity = TRACKING_CIRCULARITY
            else:
                centre = (last[0] + velocity[0], last[1] + velocity[1])
                step = float(np.hypot(*velocity))
                radius = max(seed_radius, step * 0.6) * (1.0 + 0.5 * misses)
                circularity = TRACKING_CIRCULARITY

            roi = (centre[0] - radius, centre[1] - radius,
                   centre[0] + radius, centre[1] + radius)
            found = find_ball(frame, roi, min_circularity=circularity)

            if found is None:
                misses += 1
                if misses >= 3:
                    break
                continue

            if last is not None:
                velocity = (found[0] - last[0], found[1] - last[1])
            last = found
            misses = 0
            track.append((f, found[0], found[1]))
    finally:
        cap.release()

    return track
