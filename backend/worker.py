"""Minimal worker: turn a queued upload into stored shots.

    python -m backend.worker --once           # process everything queued, then exit
    python -m backend.worker                  # keep polling every 2 s
    python -m backend.worker --ball-filter --merge-within-s 0.5

For each "queued" upload: mark it "processing", run pose extraction and shot
detection on the stored video, replace its rows in `shots`, mark it "done". Any
exception marks it "failed" and stores the message in `uploads.error`.

What it does NOT do: classify forehand/backhand (`stroke_label` stays NULL,
shown as "尚未分析") because the M3 classifier has no real validation yet;
estimate ball speed; run on a GPU; retry; schedule or bill. Shot detection
quality is what `docs/real-footage-findings.md` measured: pose only is about
half false positives, and the optional ball filter was only validated on
side-view practice clips.
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from backend import db


def process_upload(upload_id: str, *, ball_filter: bool = False,
                   merge_within_s: Optional[float] = None) -> int:
    """Run detection for one claimed upload; return the number of shots stored.

    Sets the final status itself. Exceptions are caught and recorded, never
    raised, so one bad video cannot stop the worker loop.
    """
    path = db.stored_path(upload_id)
    try:
        if path is None:
            raise FileNotFoundError(f"unknown upload id {upload_id}")
        # Imported here: pulls in MediaPipe, and tests replace these functions.
        from cv import pose_overlay
        from ml import shot_timing

        seq = pose_overlay.extract_player_landmarks(path, progress=False)
        result = shot_timing.detect_shots(seq, merge_within_s=merge_within_s)
        source = "ml.shot_timing.detect_shots"
        if merge_within_s is not None:
            source += f"(merge_within_s={merge_within_s})"
        if ball_filter:
            from ml.ball_filter import filter_shots_with_ball
            result, _ = filter_shots_with_ball(path, seq, result)
            source += "+ball_filter"
        count = db.insert_shots_from_result(upload_id, result, source=source)
        db.set_upload_status(upload_id, "done")
        return count
    except Exception as exc:  # noqa: BLE001 - one bad video must not kill the worker
        db.set_upload_status(upload_id, "failed", error=f"{type(exc).__name__}: {exc}")
        return 0


def run_once(**options) -> int:
    """Process every currently queued upload; return how many were claimed."""
    n = 0
    while (row := db.claim_next_queued()) is not None:
        process_upload(row["upload_id"], **options)
        n += 1
    return n


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.worker")
    ap.add_argument("--once", action="store_true", help="drain the queue and exit")
    ap.add_argument("--poll-s", type=float, default=2.0)
    ap.add_argument("--ball-filter", action="store_true")
    ap.add_argument("--merge-within-s", type=float, default=None)
    ap.add_argument("--requeue-processing", action="store_true",
                    help="first put 'processing' uploads back to 'queued' (only if no other worker runs)")
    args = ap.parse_args(argv)
    options = {"ball_filter": args.ball_filter, "merge_within_s": args.merge_within_s}

    if args.requeue_processing:
        print(f"requeued {db.requeue_processing()} stuck upload(s)", file=sys.stderr)
    if args.once:
        print(f"processed {run_once(**options)} upload(s)")
        return 0
    try:
        while True:
            if run_once(**options) == 0:
                time.sleep(args.poll_s)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
