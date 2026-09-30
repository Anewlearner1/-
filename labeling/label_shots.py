"""labeling/label_shots.py -- semi-automated shot-timing labeling tool.

Role brief (data-labeler): labeling cost is a named top risk
(docs/technical-plan.md §7), and its explicit mitigation is building a
semi-automated labeling tool first rather than labeling everything by hand.
This module is that tool's "semi" half: it bootstraps candidate contact
frames from ml-engineer's already-working (if unvalidated-on-real-footage)
peak detector, ``ml.shot_timing.detect_shots``, instead of making a human
mark every contact frame on a blank timeline.

Output shape is exactly the interface ml-engineer specified at the bottom
of ``ml/README.md`` under "Interface needed from data-labeler":

    {"video_id": "clip_0007", "fps": 30.0, "contact_frames": [42, 118, ...]}

Provenance (how a label was produced) is recorded in a *separate* sibling
file, ``<video_id>.meta.json``, so it never perturbs that exact shape -- see
``finalize_labels`` below and labeling/README.md.

Workflow, three stages (also usable as three CLI subcommands -- see
``labeling/README.md``):

  1. ``extract_candidates`` -- run ``extract_player_landmarks()`` +
     ``detect_shots()`` on a video, save each candidate contact frame as a
     still JPEG (frame number burned into both the filename and the image),
     and write ``candidates.json``: the bootstrapped, *unreviewed*
     candidate list plus paths to those images.
  2. Human review -- a person works through ``candidates.json``, either
     with the ``review`` CLI subcommand (``review_candidates_interactive``,
     which walks candidates one at a time with a keep/drop/skip prompt and
     can append missed frames) or by hand-editing the JSON file directly
     (set each candidate's ``"status"`` to ``"confirmed"`` or ``"rejected"``,
     or call ``add_candidate`` / append a new entry with
     ``"status": "added"`` for a contact ``detect_shots`` missed).
  3. ``finalize_labels`` -- collapse the reviewed ``candidates.json`` into
     the exact label JSON shape above, refusing to run while any candidate
     is still ``"pending"``, plus the sibling metadata file.

No real video has been labeled with this tool yet -- see
``labeling/README.md``. This is tooling only, not a delivered dataset.
"""
from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
from typing import Optional

from cv.pose_overlay import PlayerLandmarkSequence
from ml.shot_timing import ShotTimingResult, detect_shots

PENDING = "pending"
CONFIRMED = "confirmed"
REJECTED = "rejected"
ADDED = "added"
CANDIDATE_STATUSES = {PENDING, CONFIRMED, REJECTED, ADDED}

# Statuses whose frame ends up in the final label.
_KEPT_STATUSES = {CONFIRMED, ADDED}

SOURCE_BOOTSTRAPPED = "detect_shots+human_correction"


# --------------------------------------------------------------------- core


def build_candidates(result: ShotTimingResult) -> list[dict]:
    """Turn a ``detect_shots`` result into the ``candidates.json`` list shape.

    Every candidate starts out ``"pending"`` -- nothing is auto-accepted;
    see the module docstring's stage 2 for how a human resolves them.
    """
    return [
        {
            "frame": event.contact_frame,
            "time_s": event.contact_time_s,
            "peak_speed": event.peak_speed,
            "wrist": event.wrist,
            "status": PENDING,
            "image": None,
        }
        for event in result.events
    ]


def save_candidate_frame_images(
    video_path: str | Path,
    candidates: list[dict],
    images_dir: str | Path,
) -> None:
    """Grab each candidate's frame from the source video and save it as a
    still JPEG, with the frame number (and a little context) burned into
    both the image and the filename, mirroring how frames were manually
    dumped for review in the sibling tennis-form-coach project.

    Mutates ``candidates`` in place, filling in each entry's ``"image"``
    (a path relative to ``images_dir``'s parent). A frame that can't be
    read (e.g. truncated video) is left with ``"image": None`` rather than
    failing the whole extraction run.
    """
    import cv2  # local import: only needed on the real-video path

    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    try:
        for candidate in candidates:
            cap.set(cv2.CAP_PROP_POS_FRAMES, candidate["frame"])
            ok, frame = cap.read()
            if not ok:
                continue
            label = (
                f"frame {candidate['frame']}  t={candidate['time_s']:.2f}s"
                f"  wrist={candidate['wrist']}"
            )
            cv2.putText(
                frame, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                (0, 255, 0), 2, cv2.LINE_AA,
            )
            filename = f"candidate_{candidate['frame']:06d}.jpg"
            cv2.imwrite(str(images_dir / filename), frame)
            candidate["image"] = f"{images_dir.name}/{filename}"
    finally:
        cap.release()


def extract_candidates(
    video_path: str | Path,
    out_dir: str | Path,
    *,
    video_id: Optional[str] = None,
    save_images: bool = True,
    seq: Optional[PlayerLandmarkSequence] = None,
    landmark_kwargs: Optional[dict] = None,
    detect_kwargs: Optional[dict] = None,
) -> Path:
    """Stage 1: bootstrap candidate contact frames for one video.

    Runs ``cv.pose_overlay.extract_player_landmarks()`` (unless ``seq`` is
    already supplied -- used by tests and by callers that extracted
    landmarks once and want to re-run detection with different
    ``detect_kwargs`` without re-decoding the video) then
    ``ml.shot_timing.detect_shots()`` on it, and writes ``out_dir/
    candidates.json``. With ``save_images=True`` (the default) it also
    dumps each candidate frame as a still under ``out_dir/frames/``.

    Returns the path to ``candidates.json``.
    """
    video_path = Path(video_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if video_id is None:
        video_id = video_path.stem

    if seq is None:
        from cv.pose_overlay import extract_player_landmarks
        seq = extract_player_landmarks(video_path, **(landmark_kwargs or {}))

    result = detect_shots(seq, **(detect_kwargs or {}))
    candidates = build_candidates(result)

    if save_images:
        save_candidate_frame_images(video_path, candidates, out_dir / "frames")

    session = {
        "video_id": video_id,
        "video_path": str(video_path),
        "fps": seq.fps,
        "candidate_source": "detect_shots",
        "candidates": candidates,
    }
    candidates_path = out_dir / "candidates.json"
    candidates_path.write_text(json.dumps(session, indent=2))
    return candidates_path


# ------------------------------------------------------------ human review


def add_candidate(
    session: dict,
    frame: int,
    *,
    note: Optional[str] = None,
) -> dict:
    """Append a human-added candidate (a contact ``detect_shots`` missed)
    to an in-memory ``candidates.json`` session dict. Returns the new entry.
    """
    fps = session["fps"]
    entry = {
        "frame": int(frame),
        "time_s": float(frame) / fps,
        "peak_speed": None,
        "wrist": None,
        "status": ADDED,
        "image": None,
        "note": note,
    }
    session["candidates"].append(entry)
    return entry


def review_candidates_interactive(candidates_path: str | Path) -> Path:
    """Stage 2 (CLI form): walk every ``"pending"`` candidate in
    ``candidates.json`` one at a time, print its frame/time/image path, and
    ask the reviewer to keep, drop, or skip it, then offer to add missed
    frames. Writes the updated session back to the same file.

    This is the interactive half; the non-interactive alternative is
    hand-editing the JSON file directly (see module docstring) or, for
    tests/automation, calling ``add_candidate`` and setting ``"status"``
    programmatically -- ``finalize_labels`` doesn't care which path produced
    the resolved statuses.
    """
    candidates_path = Path(candidates_path)
    session = json.loads(candidates_path.read_text())

    for candidate in session["candidates"]:
        if candidate["status"] != PENDING:
            continue
        print(
            f"Frame {candidate['frame']}  t={candidate['time_s']:.2f}s  "
            f"wrist={candidate['wrist']}  image={candidate['image']}"
        )
        while True:
            answer = input("keep(k) / drop(d) / skip(s) > ").strip().lower()
            if answer in ("k", "keep"):
                candidate["status"] = CONFIRMED
                break
            if answer in ("d", "drop"):
                candidate["status"] = REJECTED
                break
            if answer in ("s", "skip"):
                break
            print("please answer k, d, or s")

    extra = input(
        "Add missed contact frames? comma-separated frame numbers, "
        "blank for none > "
    ).strip()
    for token in extra.split(","):
        token = token.strip()
        if token:
            add_candidate(session, int(token))

    candidates_path.write_text(json.dumps(session, indent=2))
    return candidates_path


# --------------------------------------------------------------- finalize


def finalize_labels(
    candidates_path: str | Path,
    out_path: Optional[str | Path] = None,
    *,
    reviewer: Optional[str] = None,
) -> tuple[dict, Path, Path]:
    """Stage 3: collapse a reviewed ``candidates.json`` into the exact
    label JSON shape ml-engineer's ml/README.md specifies, plus a sibling
    ``<video_id>.meta.json`` provenance file.

    Raises ``ValueError`` if any candidate is still ``"pending"`` -- a
    label is only emitted once every bootstrapped candidate has been
    explicitly confirmed or rejected by a human (added candidates start
    already-resolved, since adding one *is* the human decision).

    Returns ``(label_dict, label_path, meta_path)``.
    """
    candidates_path = Path(candidates_path)
    session = json.loads(candidates_path.read_text())
    candidates = session["candidates"]

    unresolved = [c for c in candidates if c["status"] == PENDING]
    if unresolved:
        frames = [c["frame"] for c in unresolved]
        raise ValueError(
            f"{len(unresolved)} candidate(s) still pending review, "
            f"resolve them before finalizing (frames: {frames})"
        )

    for c in candidates:
        if c["status"] not in CANDIDATE_STATUSES:
            raise ValueError(f"unknown candidate status: {c['status']!r}")

    contact_frames = sorted({c["frame"] for c in candidates if c["status"] in _KEPT_STATUSES})

    label = {
        "video_id": session["video_id"],
        "fps": session["fps"],
        "contact_frames": contact_frames,
    }

    if out_path is None:
        out_path = candidates_path.parent / f"{session['video_id']}.label.json"
    out_path = Path(out_path)
    out_path.write_text(json.dumps(label, indent=2))

    meta = {
        "video_id": session["video_id"],
        "source": SOURCE_BOOTSTRAPPED,
        "candidate_source": session.get("candidate_source", "detect_shots"),
        "reviewer": reviewer,
        "reviewed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "n_candidates_from_detect_shots": sum(
            1 for c in candidates if c["status"] in (CONFIRMED, REJECTED)
        ),
        "n_confirmed": sum(1 for c in candidates if c["status"] == CONFIRMED),
        "n_rejected": sum(1 for c in candidates if c["status"] == REJECTED),
        "n_added_by_human": sum(1 for c in candidates if c["status"] == ADDED),
        "candidates_file": str(candidates_path.resolve()),
    }
    meta_path = out_path.with_suffix("").with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2))

    return label, out_path, meta_path


# --------------------------------------------------------------------- CLI


def _cmd_extract(args: argparse.Namespace) -> None:
    path = extract_candidates(
        args.video,
        args.out_dir,
        video_id=args.video_id,
        save_images=not args.no_images,
    )
    print(f"wrote {path}")


def _cmd_review(args: argparse.Namespace) -> None:
    review_candidates_interactive(args.candidates)


def _cmd_finalize(args: argparse.Namespace) -> None:
    label, label_path, meta_path = finalize_labels(
        args.candidates, args.out, reviewer=args.reviewer
    )
    print(f"wrote {label_path}")
    print(f"wrote {meta_path}")
    print(json.dumps(label, indent=2))


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="label_shots",
        description="Semi-automated shot-timing labeling tool (see labeling/README.md).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser("extract", help="bootstrap candidate contact frames from a video")
    p_extract.add_argument("video", help="path to the source video")
    p_extract.add_argument("out_dir", help="directory to write candidates.json + frames/ into")
    p_extract.add_argument("--video-id", dest="video_id", default=None)
    p_extract.add_argument("--no-images", action="store_true", help="skip saving still frames")
    p_extract.set_defaults(func=_cmd_extract)

    p_review = sub.add_parser("review", help="interactively confirm/reject/add candidates")
    p_review.add_argument("candidates", help="path to candidates.json")
    p_review.set_defaults(func=_cmd_review)

    p_finalize = sub.add_parser("finalize", help="emit the final contact_frames label JSON")
    p_finalize.add_argument("candidates", help="path to a fully-reviewed candidates.json")
    p_finalize.add_argument("--out", default=None, help="output label JSON path")
    p_finalize.add_argument("--reviewer", default=None, help="human reviewer identifier")
    p_finalize.set_defaults(func=_cmd_finalize)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
