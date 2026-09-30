"""Computer-vision pipeline for Rally AI.

Modules land here as milestones are built:

  * ``player_selection`` -- pick the target player out of everyone detected
    in a frame. M1 ships a placeholder heuristic; a real detector + tracker
    (see its module docstring) replaces it later without touching callers.
  * ``pose_overlay`` -- M1 deliverable: player selection + MediaPipe pose
    estimation, rendered as a skeleton-overlay video.

Ball tracking, court-keypoint/homography, landing-point detection and the
ball-speed algorithm (M4/M5) will be added as their own modules.
"""
