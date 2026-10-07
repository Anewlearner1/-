"""Evaluate ml/stroke_classification.py against labeled clips (M3).

Label schema (additive extension of the existing M2 label JSON; see
ml/README.md). Existing fields are unchanged:

    {
      "video_id": "clip_0007",
      "fps": 30.0,
      "contact_frames": [42, 118, 203],
      "stroke_labels": ["forehand", "backhand", "other"],   # NEW, optional
      "racket_hand": "right",                               # NEW, optional
      "source": "real" | "synthetic"                        # NEW, optional
    }

* ``stroke_labels`` is aligned index-for-index with ``contact_frames``;
  each entry is "forehand" | "backhand" | "other" (serve/overhead), or null
  for "not labeled" (skipped, counted). Clips without the field are skipped.
* ``racket_hand`` (optional): used as the ``hand`` override; otherwise the
  hand is inferred, which the report flags separately.
* ``source`` (optional): anything other than "real" (including missing) is
  printed with a loud NOT-REAL-FOOTAGE banner so a synthetic/hand-made
  fixture is never mistaken for an accuracy result.

Landmarks per clip come from ``<landmarks-dir>/<video_id>.npz`` (arrays
landmarks, visibility, detected, and scalars fps, width, height; see
``save_landmarks``) or, with ``--videos-dir``, from running
``cv.pose_overlay.extract_player_landmarks`` on ``<video_id>.mp4``.

Modes:
  oracle   (default) classify at the LABELED contact frames: isolates the
           classifier from M2 detector error.
  detected run detect_shots, match each label to the nearest detected shot
           within ``--tolerance`` frames; labels with no match count as
           "missed" (reported separately and counted incorrect).

Metrics: confusion matrix (rows = truth, cols = forehand/backhand/other/
unknown), overall accuracy (unknown counts as wrong), FH-vs-BH-only
accuracy, and coverage (fraction not "unknown").

Usage:
    python3 -m ml.eval_stroke_classification labels/*.json --landmarks-dir lm/
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import numpy as np

from cv.pose_overlay import PlayerLandmarkSequence
from ml.shot_timing import ShotEvent, detect_shots
from ml.stroke_classification import LABELS, PREDICTIONS, UNKNOWN, classify_shots

MISSED = "missed"   # extra column in detected mode: no detected shot matched


def save_landmarks(seq: PlayerLandmarkSequence, path: str | Path) -> None:
    extra = {} if seq.world_landmarks is None else {"world": seq.world_landmarks}
    np.savez(path, landmarks=seq.landmarks, visibility=seq.visibility,
             detected=seq.detected, fps=seq.fps, width=seq.width, height=seq.height, **extra)


def load_landmarks(path: str | Path) -> PlayerLandmarkSequence:
    z = np.load(path)
    return PlayerLandmarkSequence(
        landmarks=z["landmarks"], visibility=z["visibility"], detected=z["detected"],
        fps=float(z["fps"]), width=int(z["width"]), height=int(z["height"]),
        world_landmarks=z["world"] if "world" in z else None)


def evaluate_clip(label: dict, seq: PlayerLandmarkSequence, *, mode: str = "oracle",
                  tolerance: int = 5) -> list[tuple[str, str]]:
    """Return (truth, predicted) pairs for one clip; predicted may be MISSED."""
    frames = label["contact_frames"]
    truths = label["stroke_labels"]
    if len(frames) != len(truths):
        raise ValueError(f"{label.get('video_id')}: stroke_labels length "
                         f"{len(truths)} != contact_frames length {len(frames)}")
    for t in truths:
        if t is not None and t not in LABELS:
            raise ValueError(f"{label.get('video_id')}: bad stroke label {t!r}; "
                             f"expected one of {LABELS} or null")
    hand = label.get("racket_hand")

    pairs: list[tuple[str, str]] = []
    if mode == "oracle":
        events = [ShotEvent(int(f), f / seq.fps, 0.0, "unknown") for f in frames]
        preds = [r.label for r in classify_shots(seq, events, hand)]
        pairs = [(t, p) for t, p in zip(truths, preds) if t is not None]
    elif mode == "detected":
        events = detect_shots(seq).events
        preds = [r.label for r in classify_shots(seq, events, hand)]
        used: set[int] = set()
        for f, t in zip(frames, truths):
            if t is None:
                continue
            best, bd = None, tolerance + 1
            for i, e in enumerate(events):
                d = abs(e.contact_frame - f)
                if i not in used and d < bd:
                    best, bd = i, d
            if best is None:
                pairs.append((t, MISSED))
            else:
                used.add(best)
                pairs.append((t, preds[best]))
    else:
        raise ValueError(f"unknown mode {mode!r}")
    return pairs


def summarize(pairs: list[tuple[str, str]]) -> dict:
    cols = list(PREDICTIONS) + ([MISSED] if any(p == MISSED for _, p in pairs) else [])
    matrix = {t: {c: 0 for c in cols} for t in LABELS}
    for t, p in pairs:
        matrix[t][p] += 1
    n = len(pairs)
    correct = sum(1 for t, p in pairs if t == p)
    fhbh = [(t, p) for t, p in pairs if t in ("forehand", "backhand")]
    answered = [(t, p) for t, p in pairs if p not in (UNKNOWN, MISSED)]
    return {
        "n_shots": n,
        "accuracy": correct / n if n else None,
        "fh_bh_accuracy": (sum(1 for t, p in fhbh if t == p) / len(fhbh)) if fhbh else None,
        "coverage": len(answered) / n if n else None,
        "confusion": matrix,
    }


def _load_clips(label_paths, landmarks_dir, videos_dir):
    """Yield (label, seq) for every label file that has stroke_labels."""
    for lp in label_paths:
        label = json.loads(Path(lp).read_text())
        if "stroke_labels" not in label:
            yield label, None
            continue
        vid = label["video_id"]
        if landmarks_dir is not None and (Path(landmarks_dir) / f"{vid}.npz").exists():
            seq = load_landmarks(Path(landmarks_dir) / f"{vid}.npz")
        elif videos_dir is not None:
            from cv.pose_overlay import extract_player_landmarks
            seq = extract_player_landmarks(Path(videos_dir) / f"{vid}.mp4")
        else:
            raise FileNotFoundError(f"no landmarks for {vid}: need <landmarks-dir>/{vid}.npz or --videos-dir")
        yield label, seq


def _balanced_cut(items: list[tuple[float, str]]) -> Optional[float]:
    """Midpoint threshold maximising balanced FH/BH accuracy (value >= cut -> forehand)."""
    vals = sorted({v for v, _ in items})
    fh = [v for v, t in items if t == "forehand"]
    bh = [v for v, t in items if t == "backhand"]
    if not fh or not bh or len(vals) < 2:
        return None
    best = None
    for a, b in zip(vals, vals[1:]):
        cut = (a + b) / 2
        bal = (np.mean([v >= cut for v in fh]) + np.mean([v < cut for v in bh])) / 2
        if best is None or bal > best[0]:
            best = (bal, cut)
    return best[1]


def evaluate_loco_3d(label_paths: list[str | Path], *, landmarks_dir=None, videos_dir=None) -> dict:
    """Leave-one-clip-out for the 3D cue: refit LATERAL_3D_FOREHAND_M on the
    other clips (balanced accuracy), classify the held-out clip (oracle mode).
    Restores the module constant afterwards."""
    from ml import stroke_classification as sc
    clips = [(l, s) for l, s in _load_clips(label_paths, landmarks_dir, videos_dir) if s is not None]
    feats = []
    for label, seq in clips:
        hand = label.get("racket_hand") or (sc.detect_racket_hand(seq)[0] or "right")
        feats.append([(sc._lateral_3d(seq, int(f), hand), t)
                      for f, t in zip(label["contact_frames"], label["stroke_labels"])])
    saved, pairs, per_clip, cuts = sc.LATERAL_3D_FOREHAND_M, [], {}, {}
    try:
        for i, (label, seq) in enumerate(clips):
            train = [(v, t) for j, fs in enumerate(feats) if j != i for v, t in fs
                     if v is not None and t in ("forehand", "backhand")]
            cut = _balanced_cut(train)
            sc.LATERAL_3D_FOREHAND_M = saved if cut is None else cut
            p = evaluate_clip(label, seq)
            pairs += p
            per_clip[label["video_id"]] = f"{sum(t == q for t, q in p)}/{len(p)}"
            cuts[label["video_id"]] = round(sc.LATERAL_3D_FOREHAND_M, 3)
    finally:
        sc.LATERAL_3D_FOREHAND_M = saved
    out = summarize(pairs)
    out.update(mode="oracle-loco-3d", per_clip=per_clip, fold_thresholds_m=cuts,
               clips_with_world_landmarks=sum(s.world_landmarks is not None for _, s in clips),
               clips_evaluated=len(clips))
    return out


def evaluate(label_paths: list[str | Path], *, landmarks_dir: Optional[str | Path] = None,
             videos_dir: Optional[str | Path] = None, mode: str = "oracle",
             tolerance: int = 5) -> dict:
    pairs: list[tuple[str, str]] = []
    skipped, sources, inferred_hand_clips, clips = [], set(), [], 0
    world_clips = 0
    for lp in label_paths:
        label = json.loads(Path(lp).read_text())
        if "stroke_labels" not in label:
            skipped.append(label.get("video_id", str(lp)))
            continue
        vid = label["video_id"]
        if landmarks_dir is not None and (Path(landmarks_dir) / f"{vid}.npz").exists():
            seq = load_landmarks(Path(landmarks_dir) / f"{vid}.npz")
        elif videos_dir is not None:
            from cv.pose_overlay import extract_player_landmarks
            seq = extract_player_landmarks(Path(videos_dir) / f"{vid}.mp4")
        else:
            raise FileNotFoundError(f"no landmarks for {vid}: need <landmarks-dir>/{vid}.npz or --videos-dir")
        sources.add(label.get("source", "unspecified"))
        world_clips += seq.world_landmarks is not None
        if "racket_hand" not in label:
            inferred_hand_clips.append(vid)
        pairs += evaluate_clip(label, seq, mode=mode, tolerance=tolerance)
        clips += 1
    out = summarize(pairs)
    out.update(mode=mode, clips_evaluated=clips, clips_skipped_no_stroke_labels=skipped,
               sources=sorted(sources), clips_with_inferred_hand=inferred_hand_clips,
               real_footage=(sources == {"real"}), clips_with_world_landmarks=world_clips)
    return out


def format_report(res: dict) -> str:
    lines = []
    if not res["real_footage"]:
        lines.append("*** NOT REAL-FOOTAGE RESULT (source=%s): logic check only, "
                     "not an accuracy figure ***" % ",".join(res["sources"] or ["none"]))
    lines.append(f"mode={res['mode']} clips={res['clips_evaluated']} shots={res['n_shots']} "
                 f"skipped_clips={len(res['clips_skipped_no_stroke_labels'])}")
    if res["n_shots"]:
        lines.append(f"accuracy={res['accuracy']:.3f}  fh_bh_accuracy="
                     f"{'n/a' if res['fh_bh_accuracy'] is None else format(res['fh_bh_accuracy'], '.3f')}"
                     f"  coverage={res['coverage']:.3f}")
        cols = list(next(iter(res["confusion"].values())).keys())
        lines.append("truth\\pred  " + "  ".join(f"{c:>9}" for c in cols))
        for t, row in res["confusion"].items():
            lines.append(f"{t:<10}  " + "  ".join(f"{row[c]:>9}" for c in cols))
    lines.append(f"world landmarks (3D cue) present for {res['clips_with_world_landmarks']}"
                 f"/{res['clips_evaluated']} clip(s); the rest use the 2D rules only")
    if res["clips_with_inferred_hand"]:
        lines.append(f"racket hand INFERRED (no racket_hand label) for {len(res['clips_with_inferred_hand'])} clip(s)")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("labels", nargs="+", help="labeled-clip JSON files")
    ap.add_argument("--landmarks-dir")
    ap.add_argument("--videos-dir")
    ap.add_argument("--mode", choices=["oracle", "detected"], default="oracle")
    ap.add_argument("--tolerance", type=int, default=5)
    ap.add_argument("--json", action="store_true", help="print raw JSON")
    ap.add_argument("--loco-3d", action="store_true",
                    help="leave-one-clip-out, refitting the 3D threshold per fold (oracle mode)")
    a = ap.parse_args(argv)
    if a.loco_3d:
        res = evaluate_loco_3d(a.labels, landmarks_dir=a.landmarks_dir, videos_dir=a.videos_dir)
        print(json.dumps(res, indent=2))
        return 0
    res = evaluate(a.labels, landmarks_dir=a.landmarks_dir, videos_dir=a.videos_dir,
                   mode=a.mode, tolerance=a.tolerance)
    print(json.dumps(res, indent=2) if a.json else format_report(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
