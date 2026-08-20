"""
投資團隊會議 — 命令列進入點。

用法：
    python team.py 2330.TW 2454.TW              # 討論指定標的
    python team.py 2330.TW --period 3mo         # 拉長取樣區間
    python team.py 2330.TW --no-cross-exam      # 只跑第一輪（省成本）
    python team.py --list-team                  # 看團隊成員背景卡

需要的環境變數：
    ANTHROPIC_API_KEY    — Anthropic API key（或先跑過 `ant auth login`）

選用：
    TEAM_EFFORT          — low | medium | high | xhigh | max（預設 high）
    REPORT_DIR           — 報告輸出目錄（預設 ./reports）
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from discussion import run_discussion
from personas import ANALYSTS, DISCLAIMER

REPORT_DIR = Path(os.environ.get("REPORT_DIR", "./reports")) / "team"


def _print_team() -> None:
    print(f"\n{'=' * 64}")
    print("  投資團隊成員（主理人：使用者本人）")
    print(f"{'=' * 64}")
    for a in ANALYSTS:
        print(f"\n【{a['name']}　{a['en_name']}】{a['school']}　權重 1.0（完全平權）")
        print(f"  資歷：{a['background']}")
        print(f"  哲學：{a['philosophy']}")
        print(f"  最在意：{'、'.join(a['key_metrics'])}")
        print(f"  忌諱：{'；'.join(a['red_lines'])}")
        print(f"  口吻：{a['voice']}")
        print(f"  已知盲點：{a['blind_spot']}")
    print(f"\n※ {DISCLAIMER}\n")


def _save(result: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / f"team_meeting_{ts}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    return path


def _print_brief(result: dict) -> None:
    """印出給主理人的決策建議書摘要。"""
    print(f"\n{'=' * 64}")
    print("  決策建議書（供主理人拍板，AI 不代為決定）")
    print(f"{'=' * 64}")

    for sym, c in result["consensus"].items():
        if "error" in c:
            print(f"\n■ {sym}：{c['error']}")
            continue

        print(f"\n■ {sym}　團隊共識：{c['team_stance']}"
              f"（分數 {c['team_score']:+.2f}｜平均信心 {c['avg_conviction']}/10）")
        print(f"  票數：買 {c['votes']['BUY']}／觀望 {c['votes']['HOLD']}／賣 {c['votes']['SELL']}"
              f"｜分歧度 {c['dispersion']}")
        if c["target_price_range"]:
            tp = c["target_price_range"]
            print(f"  目標價區間：{tp['low']} ~ {tp['high']}（中位數 {tp['median']}）")
        if c["tightest_stop_loss"] is not None:
            print(f"  最保守停損：{c['tightest_stop_loss']}")
        if c["needs_manual_review"]:
            print("  ⚠ 團隊意見分歧明顯 — 建議你逐條看完雙方論點再決定")

        for p in c["positions"]:
            mark = "↻" if p["changed_in_round2"] else " "
            print(f"   {mark} {p['name']}（{p['school']}）"
                  f"{p['stance']} {p['conviction']}/10｜{p['time_horizon']}")
            print(f"       {p['thesis']}")
            if p["changed_in_round2"]:
                print(f"       修正原因：{p['change_reason']}")

    print(f"\n※ {result['disclaimer']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="投資團隊股票討論（5 位分析師兩輪辯論，你是主理人）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("symbols", nargs="*",
                        help="要討論的股票代號，例如 2330.TW 2454.TW ^TWII")
    parser.add_argument("--period", default="1mo",
                        help="歷史資料區間（預設 1mo；可用 5d/1mo/3mo/6mo/1y）")
    parser.add_argument("--interval", default="1d",
                        help="K 棒間隔（預設 1d；可用 1h/1d/1wk）")
    parser.add_argument("--no-cross-exam", action="store_true",
                        help="跳過第二輪交叉質詢，只跑第一輪（約省一半成本）")
    parser.add_argument("--list-team", action="store_true",
                        help="列出團隊成員背景卡後結束")
    args = parser.parse_args()

    if args.list_team:
        _print_team()
        return 0

    if not args.symbols:
        parser.error("請指定至少一檔標的，例如：python team.py 2330.TW 2454.TW")

    result = run_discussion(
        args.symbols,
        period=args.period,
        interval=args.interval,
        cross_examine=not args.no_cross_exam,
    )

    _print_brief(result)

    path = _save(result)
    print(f"\n  [輸出] 結構化 JSON 已存至 {path}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n  已中斷。")
        sys.exit(130)
