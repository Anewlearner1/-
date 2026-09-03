"""
US Stock AI Investment Team — scheduler entry point.

Usage:
    python run_us_team.py --once              # one full team cycle, then exit
    python run_us_team.py --once --no-trade   # analyse and decide, but do not touch the paper portfolio
    python run_us_team.py --status            # print portfolio + goal progress (no LLM calls)
    python run_us_team.py --valuate FCEL NVDA # print bull/base/bear price targets (no LLM calls)
    python run_us_team.py --interval 240      # run every 4 hours
    python run_us_team.py --reset             # start a fresh paper portfolio (asks for confirmation)

Environment (see .env.example):
    ANTHROPIC_API_KEY      required (or `ant auth login`)
    DISCORD_WEBHOOK_URL    optional push notifications
    US_TEAM_*              model / effort / web search / paths
    RISK_*                 hard risk limits
    GOAL_*                 goal parameters
    MARKET_OPEN_ONLY=1     only run during US market hours (Mon-Fri 09:30-16:00 ET)
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
import traceback
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from us_team.config import GOAL, RUNTIME
from us_team.data import fetch_fundamentals, fetch_history
from us_team.goal import describe_goal, evaluate_goal, milestones
from us_team.llm import api_key_present
from us_team.portfolio import describe_portfolio
from us_team.report import save_report, send_discord, send_error, send_startup
from us_team.team import load_portfolio, portfolio_path, run_team
from us_team.valuation import batch_estimate, describe_valuations

ET = ZoneInfo("America/New_York")
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)

_running = True


def _handle_signal(sig, frame):
    global _running
    print(f"\n  接收到信號 {sig}，準備優雅停止...")
    _running = False


def is_market_hours(now: datetime | None = None) -> bool:
    now = now or datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def show_status() -> None:
    pf = load_portfolio()
    prices = {}
    if pf.positions:
        hist = fetch_history(list(pf.positions), period="5d", interval="1d")
        prices = {s: float(df["Close"].dropna().iloc[-1]) for s, df in hist.items() if not df.empty}
    equity = pf.equity(prices)
    goal = evaluate_goal(equity, GOAL, pf.start_date)
    print("\n# 投資組合\n" + describe_portfolio(pf.summary(prices)))
    print("\n# 目標進度\n" + describe_goal(goal))
    print("\n# 等速路徑里程碑")
    for m in milestones(GOAL, pf.start_date):
        print(f"  第 {m['month']:2d} 個月: ${m['equity_target']:,.0f}")
    print(f"\n狀態檔: {portfolio_path().resolve()}")


def show_valuation(symbols: list[str]) -> None:
    """Print bull/base/bear price targets for the given symbols. No LLM calls."""
    symbols = [s.upper() for s in symbols]
    hist = fetch_history(symbols, period="5d", interval="1d")
    prices = {s: float(df["Close"].dropna().iloc[-1]) for s, df in hist.items() if not df.empty}
    fundamentals = fetch_fundamentals(symbols)
    estimates = batch_estimate(fundamentals, prices)
    skipped = [s for s in symbols if s not in estimates]
    print("\n" + describe_valuations(estimates, skipped=skipped))
    missing_price = [s for s in symbols if s not in prices]
    if missing_price:
        print(f"\n無報價（下載失敗或代碼錯誤）: {', '.join(missing_price)}")


def reset_portfolio() -> None:
    path = portfolio_path()
    if path.exists():
        answer = input(f"確定要刪除 {path} 並重新開始模擬帳戶？(yes/no): ").strip().lower()
        if answer != "yes":
            print("已取消")
            return
        path.unlink()
    pf = load_portfolio()
    print(f"已建立新模擬帳戶：現金 ${pf.cash:,.2f}，起算日 {pf.start_date}")


def run_once(no_trade: bool) -> dict | None:
    if RUNTIME.market_open_only and not is_market_hours():
        print(f"  [跳過] 目前 {datetime.now(ET):%H:%M} ET 非美股交易時段 (09:30-16:00 ET, 週一至週五)")
        return None
    try:
        result = run_team(no_trade=no_trade)
        path = save_report(result)
        print(f"\n  報告已儲存: {path}")
        send_discord(result)
        return result
    except KeyboardInterrupt:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"\n  [錯誤] 團隊執行失敗: {e}")
        traceback.print_exc()
        send_error(f"{e}\n{traceback.format_exc()[-1200:]}")
        return None


def run_scheduler(interval_seconds: int, no_trade: bool) -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    print(f"\n  美股 AI 投資團隊啟動｜每 {interval_seconds // 60} 分鐘｜報告目錄 {RUNTIME.report_dir.resolve()}")
    print(f"  僅交易時段: {'是' if RUNTIME.market_open_only else '否'}｜交易: {'關閉' if no_trade else '模擬'}｜按 Ctrl+C 停止\n")
    send_startup(interval_seconds // 60, RUNTIME.market_open_only)

    while _running:
        run_once(no_trade)
        if not _running:
            break
        for remaining in range(interval_seconds, 0, -1):
            if not _running:
                break
            if remaining % 60 == 0 or remaining <= 10:
                mins, secs = divmod(remaining, 60)
                print(f"\r  下次執行倒計時: {mins:02d}:{secs:02d}  ", end="", flush=True)
            time.sleep(1)
    print("\n  團隊已停止。")


def main() -> None:
    parser = argparse.ArgumentParser(description="美股 AI 投資團隊 — 多角色 Claude 決策 + 模擬帳戶")
    parser.add_argument("--once", action="store_true", help="只執行一次後退出")
    parser.add_argument("--interval", type=int, default=240, metavar="MINUTES", help="執行間隔（分鐘），預設 240")
    parser.add_argument("--no-trade", action="store_true", help="只分析與決策，不更動模擬帳戶")
    parser.add_argument("--status", action="store_true", help="顯示投資組合與目標進度（不呼叫 LLM）")
    parser.add_argument("--valuate", nargs="+", metavar="SYMBOL", help="印出指定標的的三情境估值（不呼叫 LLM）")
    parser.add_argument("--reset", action="store_true", help="重置模擬帳戶")
    args = parser.parse_args()

    if args.status:
        show_status()
        return
    if args.valuate:
        show_valuation(args.valuate)
        return
    if args.reset:
        reset_portfolio()
        return

    if not api_key_present():
        print("錯誤: 找不到 Anthropic 憑證。請設定 ANTHROPIC_API_KEY 或執行 `ant auth login`。")
        sys.exit(1)

    if args.once:
        result = run_once(args.no_trade)
        sys.exit(0 if result else 1)
    run_scheduler(args.interval * 60, args.no_trade)


if __name__ == "__main__":
    main()
