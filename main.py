"""
Taiwan Stock Market AI Agent — hourly scheduler entry point.

Usage:
    python main.py                  # run once immediately, then every hour
    python main.py --once           # single run, then exit
    python main.py --interval 30    # run every 30 minutes

Environment variables required:
    ANTHROPIC_API_KEY   — your Anthropic API key

Optional:
    REPORT_DIR          — directory to save reports (default: ./reports)
    MARKET_OPEN_ONLY    — if "1", only run during Taiwan market hours 09:00-13:30 CST
"""
import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from agent import run_analysis

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
REPORT_DIR = Path(os.environ.get("REPORT_DIR", "./reports"))
MARKET_OPEN_ONLY = os.environ.get("MARKET_OPEN_ONLY", "0") == "1"

# Taiwan Stock Exchange hours: Mon-Fri 09:00–13:30 CST
MARKET_OPEN  = dtime(9, 0)
MARKET_CLOSE = dtime(13, 30)

_running = True


def _handle_signal(sig, frame):
    global _running
    print(f"\n  接收到信號 {sig}，準備優雅停止...")
    _running = False


def _is_market_hours() -> bool:
    """Check if current Taipei time is within trading hours (weekdays 09:00-13:30)."""
    now = datetime.now(TAIPEI_TZ)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _save_report(result: dict) -> Path:
    """Persist analysis report to disk as JSON + plain text."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save full JSON result (includes raw data + usage stats)
    json_path = REPORT_DIR / f"report_{ts}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        # raw_data may contain DataFrames — serialize to dict safely
        safe_result = {k: v for k, v in result.items() if k != "raw_data"}
        json.dump(safe_result, f, ensure_ascii=False, indent=2, default=str)

    # Save human-readable text report
    txt_path = REPORT_DIR / f"report_{ts}.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"台灣股市 AI 分析報告\n")
        f.write(f"生成時間: {result.get('generated_at', ts)}\n")
        f.write(f"模型: {result.get('model', 'unknown')}\n")
        f.write("=" * 60 + "\n\n")
        f.write(result.get("report", ""))

    # Keep a rolling "latest" symlink / copy for easy access
    latest_txt = REPORT_DIR / "latest_report.txt"
    latest_txt.write_text(txt_path.read_text(encoding="utf-8"), encoding="utf-8")

    return txt_path


def _print_banner(next_run_in: int | None = None):
    now = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S CST")
    print(f"\n{'─'*60}")
    print(f"  台灣股市 AI Agent | {now}")
    if next_run_in is not None:
        mins, secs = divmod(next_run_in, 60)
        print(f"  下次執行: {mins:02d}:{secs:02d} 後")
    print(f"{'─'*60}")


def run_once() -> dict | None:
    """Execute one full analysis cycle. Returns result dict or None on error."""
    if MARKET_OPEN_ONLY and not _is_market_hours():
        now = datetime.now(TAIPEI_TZ).strftime("%H:%M CST")
        print(f"  [跳過] 目前 {now} 非市場交易時間 (09:00-13:30 CST，僅週一至週五)")
        return None

    try:
        result = run_analysis()
        txt_path = _save_report(result)
        print(f"\n  報告已儲存: {txt_path}")
        print(f"\n{'='*60}")
        print("  最新分析摘要 (前 500 字):")
        print("  " + result["report"][:500].replace("\n", "\n  "))
        print(f"{'='*60}")
        return result
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"\n  [錯誤] 分析執行失敗: {e}")
        import traceback
        traceback.print_exc()
        return None


def run_scheduler(interval_seconds: int):
    """Main loop: run analysis every `interval_seconds`, save reports."""
    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    print(f"\n  台灣股市 AI Agent 啟動")
    print(f"  執行間隔: 每 {interval_seconds // 60} 分鐘")
    print(f"  報告目錄: {REPORT_DIR.resolve()}")
    print(f"  僅在市場時間執行: {'是' if MARKET_OPEN_ONLY else '否'}")
    print(f"  按 Ctrl+C 停止\n")

    while _running:
        _print_banner()
        run_once()

        if not _running:
            break

        # Count down to next run
        for remaining in range(interval_seconds, 0, -1):
            if not _running:
                break
            if remaining % 60 == 0 or remaining <= 10:
                mins, secs = divmod(remaining, 60)
                print(f"\r  下次執行倒計時: {mins:02d}:{secs:02d}  ", end="", flush=True)
            time.sleep(1)

    print("\n  Agent 已停止。")


def main():
    parser = argparse.ArgumentParser(
        description="台灣股市 AI 分析 Agent — 每小時自動執行技術分析"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只執行一次後退出",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        metavar="MINUTES",
        help="執行間隔（分鐘），預設 60",
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("錯誤: 請設定環境變數 ANTHROPIC_API_KEY")
        print("  export ANTHROPIC_API_KEY='your-api-key'")
        sys.exit(1)

    if args.once:
        result = run_once()
        sys.exit(0 if result else 1)
    else:
        run_scheduler(interval_seconds=args.interval * 60)


if __name__ == "__main__":
    main()
