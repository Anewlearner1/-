"""Compare detect_shots() against labeled contact frames.

Label shape is the one in ml/README.md (``video_id``, ``fps``,
``contact_frames``) with an optional sibling ``<video_id>.meta.json``. If the
meta says ``"complete": false`` the labels are only the confirmed contacts, so
this reports the **hit rate** and signed timing offsets of those contacts but
deliberately does NOT report precision: an unlisted detection may be a real
stroke nobody labeled.

    python -m ml.eval_shot_timing labeling/labels/*.json --videos-dir data/videos
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence


def load_label(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("video_id", "fps", "contact_frames"):
        if key not in data:
            raise ValueError(f"{path}: label is missing {key!r}")
    meta_path = Path(path).with_suffix("").with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    data["complete"] = bool(meta.get("complete", True))
    data["not_contacts"] = [int(x) for x in meta.get("not_contacts", [])]
    return data


def match_contacts(detected: Sequence[int], truth: Sequence[int]) -> list[dict]:
    """For each true contact, the nearest detected frame and the signed offset.

    ``offset = detected - truth``: negative means the detector fired early.
    A detection may be the nearest match for more than one contact.
    """
    out = []
    for t in truth:
        if not detected:
            out.append({"truth": int(t), "detected": None, "offset": None})
            continue
        n = min(detected, key=lambda d: abs(d - t))
        out.append({"truth": int(t), "detected": int(n), "offset": int(n - t)})
    return out


def match_one_to_one(detected: Sequence[int], truth: Sequence[int], tol: int) -> dict:
    """Greedy one-to-one matching: each truth and each detection is used once.

    A second detection of the same stroke (a duplicate) is therefore a false
    positive, which is what a user counting shots would experience.
    """
    pairs = sorted(((abs(d - t), d, t) for d in detected for t in truth))
    used_d, used_t, tp = set(), set(), []
    for dist, d, t in pairs:
        if dist <= tol and d not in used_d and t not in used_t:
            used_d.add(d); used_t.add(t); tp.append((int(t), int(d)))
    return {"tp": sorted(tp),
            "fp": sorted(int(d) for d in detected if d not in used_d),
            "fn": sorted(int(t) for t in truth if t not in used_t)}


def summarize(matches: list[dict], tolerances: Sequence[int] = (5, 10)) -> dict:
    offs = [m["offset"] for m in matches if m["offset"] is not None]
    summary = {"n_contacts": len(matches),
               "mean_offset_frames": round(sum(offs) / len(offs), 2) if offs else None}
    for tol in tolerances:
        hits = sum(1 for o in offs if abs(o) <= tol)
        summary[f"hit_rate_within_{tol}"] = round(hits / len(matches), 3) if matches else None
    return summary


def evaluate_detected(label: dict, detected: Sequence[int],
                      tolerances: Sequence[int] = (5, 10)) -> dict:
    matches = match_contacts(sorted(detected), label["contact_frames"])
    result = {"video_id": label["video_id"], "complete_labels": label["complete"],
              "n_detected": len(detected), "matches": matches,
              **summarize(matches, tolerances)}
    if label["not_contacts"]:
        # Frames the owner said are NOT ball contacts: a detection landing on
        # one is a known false positive even when the contact list is partial.
        tol = max(tolerances)
        hit = sorted(int(d) for d in detected
                     if any(abs(d - n) <= tol for n in label["not_contacts"]))
        result["known_false_positives"] = hit
    if label["complete"]:
        for tol in tolerances:
            m = match_one_to_one(sorted(detected), label["contact_frames"], tol)
            tp, fp, fn = len(m["tp"]), len(m["fp"]), len(m["fn"])
            result[f"within_{tol}"] = {
                "tp": tp, "fp": fp, "fn": fn,
                "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                "recall": round(tp / (tp + fn), 3) if tp + fn else None,
                "false_positive_frames": m["fp"], "missed_frames": m["fn"]}
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ml.eval_shot_timing")
    parser.add_argument("labels", nargs="+", type=Path)
    parser.add_argument("--videos-dir", type=Path, required=True)
    parser.add_argument("--hand", choices=["auto", "left", "right"], default=None)
    parser.add_argument("--merge-within-s", type=float, default=None,
                        help="drop an event this soon after the previous one (see detect_shots)")
    parser.add_argument("--ball-filter", action="store_true",
                        help="keep only events with a ball blob near a wrist (ml/ball_filter.py)")
    args = parser.parse_args(argv)

    from cv.pose_overlay import extract_player_landmarks
    from ml.shot_timing import detect_shots

    results = []
    for lab_path in args.labels:
        label = load_label(lab_path)
        video = next(iter(sorted(args.videos_dir.glob(label["video_id"] + ".*"))), None)
        if video is None:
            print(f"skip {lab_path}: no video named {label['video_id']}.* in {args.videos_dir}",
                  file=sys.stderr)
            continue
        seq = extract_player_landmarks(video, progress=False)
        result = detect_shots(seq, hand=args.hand, merge_within_s=args.merge_within_s)
        if args.ball_filter:
            from ml.ball_filter import filter_shots_with_ball
            result, _ = filter_shots_with_ball(video, seq, result)
        found = [e.contact_frame for e in result.events]
        results.append(evaluate_detected(label, found))
    print(json.dumps(results, indent=2))
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
