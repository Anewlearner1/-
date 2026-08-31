"""
結算與計分 —— 投資團隊的體檢報告。

用法：
    python score.py --record            把 reports/team/ 裡的會議收進帳本
    python score.py --resolve           結算所有已到期的判斷（抓真實價格）
    python score.py                     顯示計分卡
    python score.py --pending           看還沒到期的判斷

沒有 --resolve 過的判斷不會出現在計分卡裡。統計要有意義，
至少需要 30 筆以上已結算的判斷 —— 在那之前所有數字都只是雜訊。
"""
import argparse
import glob
import json
import sys

import envfile  # noqa: F401 — 先載入 .env
from ledger import Ledger

MIN_SAMPLES = 30  # 低於此數的統計不具參考價值，會明確標示


def _price_fn(symbol, start, end):
    """抓結算區間的日 K。只在 --resolve 時才需要網路。"""
    from fetcher import fetch_yfinance_data
    import pandas as pd
    days = (end - start).days + 5
    period = f"{max(days, 5)}d" if days <= 700 else "5y"
    data = fetch_yfinance_data([symbol], period=period, interval="1d")
    df = data.get(symbol)
    if df is None or df.empty:
        return None
    idx = df.index.tz_localize(None) if df.index.tz is not None else df.index
    return df[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]


def _print_scorecard(lg: Ledger) -> int:
    card = lg.scorecard()
    if not card:
        print("  帳本裡還沒有已結算的判斷。先 --record 再 --resolve。")
        return 1

    total = sum(s["n"] for s in card.values())
    print(f"\n{'=' * 70}\n  分析師計分卡（已結算 {total} 筆）\n{'=' * 70}")
    if total < MIN_SAMPLES:
        print(f"  ⚠ 樣本僅 {total} 筆，低於 {MIN_SAMPLES} 筆門檻 —— "
              f"以下數字統計上不具參考價值，不要據此調整權重。\n")

    rows = sorted(card.items(), key=lambda kv: kv[1]["avg_r"] or -99, reverse=True)
    print(f"  {'分析師':<10} {'筆數':>4} {'勝率':>7} {'平均R':>7} {'累計R':>7} "
          f"{'賠率':>6} {'棄權':>5}")
    for aid, s in rows:
        f = lambda v, p=".2f": "  —  " if v is None else format(v, p)
        print(f"  {aid:<10} {s['n']:>4} {f(s['win_rate'], '.0%'):>7} "
              f"{f(s['avg_r']):>7} {f(s['total_r']):>7} "
              f"{f(s['payoff_ratio'], '.1f'):>6} {s['abstained']:>5}")

    print("\n  信心度校準（高信心該比低信心準，否則信心度是雜訊）")
    for aid, s in rows:
        c = s["calibration"]
        if not (c["high"]["n"] or c["low"]["n"]):
            continue
        g = lambda b: "—" if b["win_rate"] is None else f"{b['win_rate']:.0%}({b['n']}筆)"
        print(f"  {aid:<10} 高信心 {g(c['high']):>12}   低信心 {g(c['low']):>12}")

    print("\n  平均R 就是期望值：>0 才代表這位分析師的判斷長期有正貢獻。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="投資團隊判斷的結算與計分")
    ap.add_argument("--record", action="store_true", help="把會議報告收進帳本")
    ap.add_argument("--resolve", action="store_true", help="結算已到期的判斷")
    ap.add_argument("--pending", action="store_true", help="列出未到期的判斷")
    ap.add_argument("--ledger", default="reports/ledger.jsonl")
    a = ap.parse_args()
    lg = Ledger(a.ledger)

    if a.record:
        n = sum(lg.record(json.load(open(f, encoding="utf-8")))
                for f in sorted(glob.glob("reports/team/team_meeting_*.json")))
        print(f"  [帳本] 新增 {n} 筆判斷，目前未結算 {len(lg.pending())} 筆")

    if a.resolve:
        print("  [結算] 抓取價格中...")
        print(f"  [結算] 完成 {lg.resolve_due(_price_fn)} 筆")

    if a.pending:
        for r in sorted(lg.pending(), key=lambda x: x["due_date"]):
            print(f"  {r['due_date']}  {r['symbol']:<10} {r['analyst']:<10} "
                  f"{r['stance']:<5} 進場{r['entry']:>8.1f} "
                  f"停損{r['stop'] if r['stop'] else '—'}")
        return 0

    return _print_scorecard(lg) if not (a.record or a.resolve) else 0


if __name__ == "__main__":
    sys.exit(main())
