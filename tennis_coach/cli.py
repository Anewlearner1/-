"""Command-line entry point for tennis-form-coach."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tennis-coach",
        description="網球動作分析 — 從影片量測揮拍速度、擊球初速與擊球角度",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "範例:\n"
            "  tennis-coach speed serve.mp4\n"
            "  tennis-coach speed serve.mp4 --hand right --json out/speed.json\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("speed", help="量測揮拍速度、擊球初速與擊球角度")
    s.add_argument("video", help="影片路徑 (mp4 / mov / avi)")
    s.add_argument("--hand", default="auto", choices=["auto", "right", "left"],
                   help="指定持拍手，預設自動判別")
    s.add_argument("--json", default=None, metavar="FILE",
                   help="另外輸出完整結果 JSON")
    s.add_argument("--racket-length", type=float, default=None, metavar="M",
                   help="手腕到拍面中心的距離（公尺），預設 0.45；此值與拍頭速度成正比")
    s.add_argument("--model-complexity", type=int, default=2, choices=[0, 1, 2],
                   help="姿態模型精度，2 最準但最慢，預設 2")
    s.add_argument("--max-frames", type=int, default=None, metavar="N",
                   help="只分析前 N 幀（快速測試用）")
    s.add_argument("--quiet", "-q", action="store_true", help="不顯示進度")
    return parser


def cmd_speed(args: argparse.Namespace) -> int:
    from . import kinetics
    from .features import extract_features
    from .pose import estimate_pose
    from .segment import find_swings

    video = Path(args.video)
    if not video.exists():
        print(f"錯誤: 找不到影片檔 {video}", file=sys.stderr)
        return 2

    if args.racket_length is not None:
        if not 0.1 <= args.racket_length <= 1.0:
            print("錯誤: --racket-length 應介於 0.1 至 1.0 公尺", file=sys.stderr)
            return 2
        kinetics.RACKET_LENGTH_M = args.racket_length

    verbose = not args.quiet
    if verbose:
        print(f"\n  分析影片: {video}")

    seq = estimate_pose(video, model_complexity=args.model_complexity,
                        max_frames=args.max_frames, progress=verbose)
    feats = extract_features(seq, hand=None if args.hand == "auto" else args.hand)
    swings = find_swings(feats)

    quality = seq.quality()
    if quality["usable"] != "True":
        print(f"\n  ⚠️  追蹤品質偏低（偵測率 {quality['detected_ratio']:.0%}，"
              f"平均可見度 {quality['mean_visibility']:.2f}）— 數值僅供參考。")

    if not swings:
        print("\n  未偵測到揮拍動作。", file=sys.stderr)
        return 1

    results = [kinetics.measure_swing(seq, feats, sw, video) for sw in swings]
    print_speed_summary(results, feats, quality)

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "video": str(video),
            "fps": round(seq.fps, 2),
            "hand": feats.hand,
            "torso_scale_m": round(feats.scale, 4),
            "racket_length_m": kinetics.RACKET_LENGTH_M,
            "tracking_quality": quality,
            "swings": [r.to_dict() for r in results],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        if verbose:
            print(f"  [JSON] {path}")
    return 0


def print_speed_summary(results, feats, quality) -> None:
    """Terminal table, so the CLI is useful without opening anything else."""
    line = "─" * 62
    print(f"\n{line}")
    print(f"  慣用手：{'右手' if feats.hand == 'right' else '左手'}　"
          f"揮拍次數：{len(results)}　"
          f"軀幹長估計：{feats.scale:.2f} m")
    print(line)

    headers = ["#", "觸球幀", "拍頭速度", "手腕速度", "擊球初速", "擊球角度"]
    widths = [4, 8, 12, 12, 12, 10]
    print("  " + "".join(_pad(h, w) for h, w in zip(headers, widths)))

    for i, r in enumerate(results, start=1):
        cells = [
            str(i),
            str(r.contact_frame),
            _fmt(r.racket_speed_kmh, "km/h"),
            _fmt(r.wrist_speed_kmh, "km/h"),
            _fmt(r.ball_speed_kmh, "km/h"),
            _fmt(r.launch_angle_deg, "°"),
        ]
        print("  " + "".join(_pad(c, w) for c, w in zip(cells, widths)))

    notes = [(i, n) for i, r in enumerate(results, start=1) for n in r.notes]
    if notes:
        print(f"\n{line}")
        print("  備註")
        print(line)
        for i, note in notes:
            print(f"  第 {i} 拍：{note}")
    print()


def _display_width(text: str) -> int:
    """Columns a string occupies in a terminal.

    CJK characters are one `len()` unit but two columns wide, so padding by
    `len()` alone leaves every column with Chinese in it visibly misaligned.
    """
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(1, width - _display_width(text))


def _fmt(value: float | None, unit: str) -> str:
    return "—" if value is None else f"{value:.0f} {unit}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "speed":
            return cmd_speed(args)
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f"錯誤: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中止。", file=sys.stderr)
        return 130
    return 2


if __name__ == "__main__":
    sys.exit(main())
