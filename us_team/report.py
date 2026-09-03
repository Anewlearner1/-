"""Report assembly (Markdown + JSON) and Discord delivery for the US team."""
from __future__ import annotations

import json
import time
from pathlib import Path

from notifier import _post, _split_message, COLOR_BLUE, COLOR_GREEN, COLOR_RED, COLOR_YELLOW
from .config import RUNTIME


def build_markdown(result: dict) -> str:
    pf = result["portfolio"]
    goal = result["goal"]
    dec = result["decision"]
    rv = result["risk_verdict"]

    lines = [
        f"# 美股 AI 投資團隊報告 — {result['generated_at']}",
        f"模型: {result['model']}" + ("｜模式: 不交易 (--no-trade)" if result.get("no_trade") else ""),
        "",
        "## 目標進度",
        result["goal_text"],
        "",
        "## 投資組合",
        f"- 權益 **${pf['equity']:,.2f}**｜現金 ${pf['cash']:,.2f} ({pf['cash_pct']:.1f}%)｜曝險 {pf['gross_exposure_pct']:.1f}%",
        f"- 累計報酬 **{pf['total_return_pct']:+.2f}%**｜回撤 {pf['drawdown_pct']:.1f}%｜已實現 ${pf['realized_pnl']:,.2f}",
    ]
    if pf["positions"]:
        lines.append("")
        lines.append("| 標的 | 股數 | 成本 | 現價 | 權重 | 損益 | 停損 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for r in pf["positions"]:
            lines.append(f"| {r['symbol']} | {r['shares']:g} | {r['avg_cost']:.2f} | {r['price']:.2f} | "
                         f"{r['weight_pct']:.1f}% | {r['pnl_pct']:+.1f}% | {r['stop_loss'] or '-'} |")
    else:
        lines.append("- 無持股（全現金）")

    if result.get("stop_fills"):
        lines += ["", "## 停損執行"]
        for t in result["stop_fills"]:
            lines.append(f"- SELL {t['symbol']} {t['shares']:g} @ {t['price']:.2f}｜實現 {t['realized_pnl']:+.2f}")

    if result.get("valuations"):
        lines += ["", "## 三情境估值模型（樂觀/中立/保守）"]
        lines.append("| 標的 | 方法 | 現價 | 保守目標價 | 中立目標價 | 樂觀目標價 | 機率加權目標價 | 加權隱含報酬 |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
        for sym, v in result["valuations"].items():
            lines.append(
                f"| {sym} | {v['method']} | {v['current_price']} | {v['bear']['target_price']} | "
                f"{v['base']['target_price']} | {v['bull']['target_price']} | "
                f"{v['probability_weighted_price']} | {v['probability_weighted_return_pct']:+.0f}% |"
            )

    lines += [
        "",
        f"## PM 決策 — 立場: {dec['market_stance']}",
        dec["summary"],
        "",
    ]
    if dec["orders"]:
        lines.append("| 動作 | 標的 | 目標權重 | 停損 | 信心 | 期間 | 論點 |")
        lines.append("|---|---|---:|---:|---:|---|---|")
        for o in dec["orders"]:
            stop = f"-{o['stop_loss_pct']:.0f}%" if o.get("stop_loss_pct") is not None else "-"
            lines.append(f"| {o['action']} | {o['symbol']} | {o['target_weight_pct']:.0f}% | {stop} | "
                         f"{o['confidence']}/10 | {o['time_horizon']} | {o['thesis']} |")
    lines += [
        "",
        f"**風險註記:** {dec['risk_notes']}",
        "",
        f"**目標評估:** {dec['goal_assessment']}",
        "",
        "## 風控引擎執行結果",
        result["risk_engine"]["text"],
        "",
        f"## 風險長裁決 — {'核准' if rv['approved'] else '未核准'}｜風險分數 {rv['overall_risk_score']}/10｜"
        f"最大曝險 {rv['max_new_exposure_pct']:.0f}%",
        f"- 否決: {', '.join(rv['vetoed_symbols']) or '無'}",
        f"- 要求修正: {rv['required_changes']}",
        f"- 理由: {rv['rationale']}",
        "",
        "## 多方研究員",
        result["bull"],
        "",
        "## 空方研究員",
        result["bear"],
    ]
    for key, a in result["analysts"].items():
        lines += ["", f"## {a['title']}", a["report"]]

    if result.get("data_errors"):
        lines += ["", "## 資料缺失", *[f"- {e}" for e in result["data_errors"]]]

    u = result["usage"]
    lines += ["", "---",
              f"呼叫 {u['calls']} 次｜輸入 {u['input_tokens']:,}｜輸出 {u['output_tokens']:,}"
              f"｜快取讀取 {u['cache_read']:,}｜網路搜尋 {u['web_searches']}"]
    return "\n".join(lines)


def save_report(result: dict, report_dir: Path | None = None) -> Path:
    report_dir = report_dir or RUNTIME.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = result["generated_at"].replace(":", "").replace("-", "").replace("T", "_")
    md_path = report_dir / f"us_team_{ts}.md"
    md_path.write_text(build_markdown(result), encoding="utf-8")
    json_path = report_dir / f"us_team_{ts}.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (report_dir / "latest.md").write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    return md_path


# --------------------------------------------------------------------------- #
# Discord
# --------------------------------------------------------------------------- #
def send_discord(result: dict) -> bool:
    pf = result["portfolio"]
    goal = result["goal"]
    dec = result["decision"]
    status_color = {"ahead": COLOR_GREEN, "on_track": COLOR_BLUE, "behind": COLOR_YELLOW,
                    "reached": COLOR_GREEN, "expired": COLOR_RED}.get(goal["status"], COLOR_BLUE)

    fields = [
        {"name": "💰 權益", "value": f"`${pf['equity']:,.0f}` ({pf['total_return_pct']:+.1f}%)", "inline": True},
        {"name": "🎯 目標進度", "value": f"`{goal['progress_pct']:.1f}%`｜{goal['status']}", "inline": True},
        {"name": "📉 回撤", "value": f"`{pf['drawdown_pct']:.1f}%`", "inline": True},
        {"name": "🧭 立場", "value": dec["market_stance"], "inline": True},
        {"name": "🛡️ 風險長", "value": f"{'核准' if result['risk_verdict']['approved'] else '未核准'}"
                                     f"｜{result['risk_verdict']['overall_risk_score']}/10", "inline": True},
        {"name": "📦 曝險", "value": f"`{pf['gross_exposure_pct']:.0f}%`", "inline": True},
    ]
    fills = result["risk_engine"]["fills"]
    if fills:
        fields.append({"name": "✅ 本輪成交", "inline": False,
                       "value": "\n".join(f"`{f['side']} {f['symbol']} {f['shares']:g} @ {f['price']:.2f}`" for f in fills)[:1000]})
    if pf["positions"]:
        fields.append({"name": "📊 持股", "inline": False,
                       "value": "\n".join(f"`{r['symbol']}` {r['weight_pct']:.0f}% ({r['pnl_pct']:+.1f}%)" for r in pf["positions"])[:1000]})

    embed = {
        "title": "🇺🇸 美股 AI 投資團隊報告",
        "description": (dec["summary"][:1500] + ("\n\n_（--no-trade 模式，未執行）_" if result.get("no_trade") else "")),
        "color": status_color,
        "fields": fields,
        "footer": {"text": f"{result['generated_at']}｜{result['model']}"},
    }
    if not _post({"embeds": [embed]}):
        return False

    detail = "\n".join([
        "**風控引擎**", result["risk_engine"]["text"], "",
        "**目標評估**", dec["goal_assessment"], "",
        "**風險註記**", dec["risk_notes"],
    ])
    for i, chunk in enumerate(_split_message(detail), 1):
        _post({"content": chunk})
        time.sleep(0.8)
    return True


def send_error(message: str) -> bool:
    return _post({"embeds": [{
        "title": "❌ 美股投資團隊執行錯誤",
        "description": f"```\n{message[:1800]}\n```",
        "color": COLOR_RED,
    }]})


def send_startup(interval_minutes: int, market_open_only: bool) -> bool:
    return _post({"embeds": [{
        "title": "🚀 美股 AI 投資團隊已啟動",
        "description": f"每 **{interval_minutes} 分鐘**執行一次。" + ("僅在美股交易時段執行。" if market_open_only else ""),
        "color": COLOR_BLUE,
        "fields": [
            {"name": "團隊", "value": "宏觀策略師・基本面・技術面・催化劑・多方・空方・風險長・PM", "inline": False},
            {"name": "目標", "value": "$30,000 → $1,000,000 / 3 年（模擬帳戶）", "inline": False},
        ],
    }]})
