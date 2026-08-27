"""
投資團隊會議 — 命令列進入點。

用法：
    python team.py 2330.TW 2454.TW              # 討論指定標的
    python team.py 2330.TW --period 3mo         # 拉長取樣區間
    python team.py 2330.TW --no-cross-exam      # 只跑第一輪（省成本）
    python team.py --list-team                  # 看團隊成員背景卡

預設不需要 API key —— 走本機已登入的 Claude Code CLI（吃你的 Claude 訂閱額度）：
    npm install -g @anthropic-ai/claude-code
    claude login

選用環境變數：
    TEAM_BACKEND         — claude_cli（預設，免 API key）| api（用 ANTHROPIC_API_KEY）
    TEAM_SERIAL          — 設 1 等同 --serial
    TEAM_STAGGER         — 平行啟動的間隔秒數（預設 2.0）
    TEAM_WEB_SEARCH      — 設 0 等同 --no-web-search（預設開啟）
    TEAM_MODEL           — 模型；claude_cli 用別名如 opus，api 用完整 ID
    TEAM_EFFORT          — low | medium | high | xhigh | max（預設 high）
    REPORT_DIR           — 報告輸出目錄（預設 ./reports）
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import envfile  # noqa: F401 — 必須在其他專案模組之前，先載入 .env
import backends
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


def _dump_packet(symbols: list[str], period: str, interval: str) -> int:
    """只抓資料、不呼叫模型，把資料包存成 JSON。"""
    from discussion import build_market_packet, format_packet

    packet = build_market_packet(symbols, period=period, interval=interval)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"packet_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")

    print(format_packet(packet))
    print(f"\n  [輸出] 資料包已存至 {path}")
    print("  用法：python team.py --packet " + str(path))
    return 0


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
            for x in c.get("excluded", []):
                print(f"  ⚠ {x['name']}：{x['reason']}")
            continue

        print(f"\n■ {sym}　團隊共識：{c['team_stance']}"
              f"（分數 {c['team_score']:+.2f}｜平均信心 {c['avg_conviction']}/10）")
        print(f"  票數：買 {c['votes']['BUY']}／觀望 {c['votes']['HOLD']}／賣 {c['votes']['SELL']}"
              f"｜計入 {c['voter_count']}/{c['expected_voters']} 人｜分歧度 {c['dispersion']}")
        for x in c["excluded"]:
            print(f"  ⚠ 未計入 — {x['name']}：{x['reason']}")
        for w in c["data_warnings"]:
            print(f"  ⚠ {w}")
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
            if p.get("web_sources"):
                print(f"       來源：{'；'.join(p['web_sources'])}")

    print(f"\n※ {result['disclaimer']}")
    if "含網路搜尋" in result.get("backend", ""):
        print("※ 部分論點來自即時網路搜尋，來源未經人工查核，"
              "使用前請自行核實再作為決策依據。")


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
    parser.add_argument("--serial", action="store_true",
                        help="五位分析師改為一個一個跑。慢很多，但完全避開多個 "
                             "claude 行程同時續期登入的競態（登入被撤銷時先用這個）")
    parser.add_argument("--no-web-search", action="store_true",
                        help="關閉網路搜尋，只根據資料包內容判斷（更快更省，"
                             "但抓不到資料包之外的最新消息）")
    parser.add_argument("--dump-packet", action="store_true",
                        help="只抓行情資料並存成 JSON 後結束，不呼叫任何模型。"
                             "用於在有網路的機器上產出資料包，拿到別處開會")
    parser.add_argument("--packet", metavar="FILE",
                        help="改用既有的資料包 JSON 開會，完全不連網抓資料")
    parser.add_argument("--list-team", action="store_true",
                        help="列出團隊成員背景卡後結束")
    args = parser.parse_args()

    if args.list_team:
        _print_team()
        return 0

    if args.dump_packet:
        if not args.symbols:
            parser.error("--dump-packet 需要指定標的，例如：--dump-packet 3037.TW")
        return _dump_packet(args.symbols, args.period, args.interval)

    packet = None
    if args.packet:
        if args.symbols:
            parser.error("--packet 已指定標的，請勿再於命令列給標的（會被忽略）")
        packet = json.loads(Path(args.packet).read_text(encoding="utf-8"))
        print(f"  [資料包] {args.packet} — 標的 {', '.join(packet['symbols'])}")
    elif not args.symbols:
        parser.error("請指定至少一檔標的，例如：python team.py 2330.TW 2454.TW")

    if args.serial:
        backends.SERIAL = True
    if args.no_web_search:
        backends.WEB_SEARCH = False

    result = run_discussion(
        args.symbols,
        period=args.period,
        interval=args.interval,
        cross_examine=not args.no_cross_exam,
        packet=packet,
    )

    _print_brief(result)

    path = _save(result)
    print(f"\n  [輸出] 結構化 JSON 已存至 {path}")

    # 全部標的都拿不到可用評分時以非 0 結束，讓排程或腳本能察覺
    if all("error" in c for c in result["consensus"].values()):
        print("  [失敗] 沒有任何標的取得可用的團隊評分")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n  已中斷。")
        sys.exit(130)
