"""One-command M2/M3 acceptance run against ADR 0002 / 0003 (frozen config).

    python -m ml.acceptance labeling/labels/acc*.json --videos-dir C:\\rally-videos

What counts (docs/acceptance-footage-spec.md, ADR 0002):
  * a clip is eligible only if its label is COMPLETE, numbered in OpenCV
    decoder frames, has a racket_hand, and the video passes the 60 fps gate;
    anything else is listed as excluded with the reason, never silently used;
  * at least MIN_CLIPS eligible clips, else the verdict is "insufficient";
  * M2: pooled one-to-one matching at +/-0.33 s; precision and recall >= 0.90;
  * M3: forehand/backhand accuracy >= 0.85 over the labeled FH/BH hits that the
    detector located (matched). "other" (serve/overhead) is excluded.

Frozen config: FROZEN holds every setting that affects the result. Before
scoring, the live module constants are compared with it; any drift makes the
run INVALID (someone changed a threshold after the freeze). Changing FROZEN is
a deliberate act recorded in docs/acceptance-freeze.md, and per the spec the
acceptance clips used before such a change become development footage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Optional

FROZEN = {
    "tolerance_s": 0.33,
    "detection": {"merge_within_s": 0.5, "merge_keep": "earlier", "ball_filter": True},
    "ball_filter.WINDOW_S": 8 / 30.0,
    "stroke.LATERAL_3D_FOREHAND_M": 0.28,
    "stroke.WRIST_GAP_BACKHAND": 0.30,
    "stroke.LAT3D_WINDOW_S": 0.4,
    "stroke.LAT3D_GAP_S": 0.07,
    "stroke.CONTACT_RADIUS_S": 2 / 30.0,
}
THRESHOLDS = {"m2_precision": 0.90, "m2_recall": 0.90, "m3_accuracy": 0.85}
MIN_CLIPS = 10


def config_drift() -> list[str]:
    """Live settings that differ from FROZEN (empty list = no drift)."""
    from ml import ball_filter, stroke_classification as sc
    live = {
        "ball_filter.WINDOW_S": ball_filter.WINDOW_S,
        "stroke.LATERAL_3D_FOREHAND_M": sc.LATERAL_3D_FOREHAND_M,
        "stroke.WRIST_GAP_BACKHAND": sc.WRIST_GAP_BACKHAND,
        "stroke.LAT3D_WINDOW_S": sc.LAT3D_WINDOW_S,
        "stroke.LAT3D_GAP_S": sc.LAT3D_GAP_S,
        "stroke.CONTACT_RADIUS_S": sc.CONTACT_RADIUS_S,
    }
    return [f"{k}: frozen {FROZEN[k]!r}, live {v!r}" for k, v in live.items()
            if abs(float(v) - float(FROZEN[k])) > 1e-12]


def _eligibility(label: dict, meta: dict, fps_ok: Optional[bool]) -> Optional[str]:
    """Reason the clip is NOT eligible, or None if it is."""
    if not meta.get("complete"):
        return "label not complete (every hit must be labeled)"
    if meta.get("frame_numbering") != "opencv_decoder":
        return "label not in OpenCV decoder frame numbers"
    if label.get("racket_hand") not in ("left", "right"):
        return "no racket_hand"
    if "stroke_labels" not in label or len(label["stroke_labels"]) != len(label["contact_frames"]):
        return "stroke_labels missing or misaligned"
    if fps_ok is False:
        return "video fails the 60 fps gate"
    return None


def _default_fps_ok(video: Path) -> bool:
    from backend.upload_quality import check_upload_quality
    return check_upload_quality(video).fps.status.value == "pass"


def _default_detect(video: Path):
    """(seq, events) with the FROZEN detection settings."""
    from cv.pose_overlay import extract_player_landmarks
    from ml.ball_filter import filter_shots_with_ball
    from ml.shot_timing import detect_shots
    d = FROZEN["detection"]
    seq = extract_player_landmarks(video, progress=False)
    result = detect_shots(seq, merge_within_s=d["merge_within_s"], merge_keep=d["merge_keep"])
    if d["ball_filter"]:
        result, _ = filter_shots_with_ball(video, seq, result)
    return seq, list(result.events)


def run_acceptance(label_paths: list[Path], videos_dir: Path, *,
                   detect: Callable = _default_detect,
                   fps_ok: Callable[[Path], bool] = _default_fps_ok,
                   min_clips: int = MIN_CLIPS) -> dict:
    from ml.eval_shot_timing import match_one_to_one, seconds_to_frames
    from ml.stroke_classification import classify_shots

    drift = config_drift()
    clips, excluded = [], {}
    tp = fp = fn = 0
    m3_pairs: list[tuple[str, str]] = []
    for lp in sorted(Path(p) for p in label_paths):
        label = json.loads(lp.read_text(encoding="utf-8"))
        meta_path = lp.with_suffix("").with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        video = Path(videos_dir) / f"{label['video_id']}.mp4"
        if not video.exists():
            matches = sorted(Path(videos_dir).glob(f"{label['video_id']}.*"))
            video = matches[0] if matches else video
        if not video.exists():
            excluded[label["video_id"]] = f"video not found in {videos_dir}"
            continue
        reason = _eligibility(label, meta, fps_ok(video))
        if reason:
            excluded[label["video_id"]] = reason
            continue
        seq, events = detect(video)
        tol = seconds_to_frames(FROZEN["tolerance_s"], float(label["fps"]))
        detected = sorted(int(e.contact_frame) for e in events)
        m = match_one_to_one(detected, list(label["contact_frames"]), tol)
        tp, fp, fn = tp + len(m["tp"]), fp + len(m["fp"]), fn + len(m["fn"])
        by_frame = {int(e.contact_frame): e for e in events}
        truth_of = dict(zip(label["contact_frames"], label["stroke_labels"]))
        located = [(truth_of[t], by_frame[d]) for t, d in m["tp"]
                   if truth_of[t] in ("forehand", "backhand")]
        preds = classify_shots(seq, [e for _, e in located], label["racket_hand"]) if located else []
        pairs = [(t, r.label) for (t, _), r in zip(located, preds)]
        m3_pairs += pairs
        clips.append({"video_id": label["video_id"], "tp": len(m["tp"]), "fp": len(m["fp"]),
                      "fn": len(m["fn"]), "m3_correct": sum(t == p for t, p in pairs),
                      "m3_n": len(pairs)})

    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    m3 = sum(t == p for t, p in m3_pairs) / len(m3_pairs) if m3_pairs else None
    checks = {
        "m2_precision": precision is not None and precision >= THRESHOLDS["m2_precision"],
        "m2_recall": recall is not None and recall >= THRESHOLDS["m2_recall"],
        "m3_accuracy": m3 is not None and m3 >= THRESHOLDS["m3_accuracy"],
    }
    if drift:
        verdict = "INVALID: config changed since freeze"
    elif len(clips) < min_clips:
        verdict = f"INSUFFICIENT: {len(clips)} eligible clip(s), need {min_clips}"
    else:
        verdict = "PASS" if all(checks.values()) else "FAIL"
    return {"verdict": verdict, "config_drift": drift, "eligible_clips": len(clips),
            "excluded": excluded, "m2": {"tp": tp, "fp": fp, "fn": fn,
                                          "precision": precision, "recall": recall},
            "m3": {"n": len(m3_pairs), "accuracy": m3}, "checks": checks,
            "thresholds": THRESHOLDS, "frozen": FROZEN, "per_clip": clips}


def format_report(r: dict) -> str:
    f = lambda v: "n/a" if v is None else f"{v:.3f}"
    lines = [f"驗收結果 / verdict: {r['verdict']}",
             f"eligible clips: {r['eligible_clips']} (need {MIN_CLIPS})",
             f"M2 precision {f(r['m2']['precision'])} (>= {THRESHOLDS['m2_precision']})  "
             f"recall {f(r['m2']['recall'])} (>= {THRESHOLDS['m2_recall']})  "
             f"[tp {r['m2']['tp']} fp {r['m2']['fp']} fn {r['m2']['fn']}]",
             f"M3 accuracy {f(r['m3']['accuracy'])} (>= {THRESHOLDS['m3_accuracy']}) "
             f"on {r['m3']['n']} located FH/BH hits"]
    for c in r["per_clip"]:
        lines.append(f"  {c['video_id']}: tp {c['tp']} fp {c['fp']} fn {c['fn']}  "
                     f"FH/BH {c['m3_correct']}/{c['m3_n']}")
    for vid, why in r["excluded"].items():
        lines.append(f"  EXCLUDED {vid}: {why}")
    for d in r["config_drift"]:
        lines.append(f"  CONFIG DRIFT {d}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ml.acceptance", description=__doc__.split("\n")[0])
    ap.add_argument("labels", nargs="+", type=Path)
    ap.add_argument("--videos-dir", type=Path, required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    labels = [p for p in a.labels if not p.name.endswith(".meta.json")]
    r = run_acceptance(labels, a.videos_dir)
    print(json.dumps(r, indent=2, ensure_ascii=False) if a.json else format_report(r))
    return 0 if r["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
