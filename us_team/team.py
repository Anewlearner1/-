"""Orchestrator: one full team cycle.

    data → valuation scenarios → stop-loss sweep → 4 analysts (parallel) → bull → bear
         → risk manager (structured) → PM (structured)
         → deterministic risk engine → paper fills → report
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from .config import GOAL, MODEL, RISK, RUNTIME, WATCHLIST
from .data import MarketPacket, collect_market_data, format_packet
from .goal import describe_goal, evaluate_goal, milestones
from .llm import TeamLLM
from .portfolio import Portfolio, describe_portfolio
from .risk import RiskReport, apply_fills, describe_limits, plan_orders
from .roles import ANALYSTS, BEAR, BULL, PM, RISK as RISK_ROLE
from .schemas import PMDecision, RiskVerdict
from .valuation import ValuationEstimate, batch_estimate, describe_valuations


def portfolio_path() -> Path:
    return RUNTIME.state_dir / "portfolio.json"


def load_portfolio() -> Portfolio:
    return Portfolio.load_or_create(portfolio_path(), GOAL.start_capital, GOAL.start_date)


def _shared_context(packet: MarketPacket, pf_text: str, goal_text: str, limits_text: str,
                    valuation_text: str) -> str:
    return "\n\n".join([
        format_packet(packet),
        "# 三情境估值模型（樂觀/中立/保守，程式計算）\n" + valuation_text,
        "# 目前投資組合（模擬帳戶）\n" + pf_text,
        "# 委託目標進度\n" + goal_text,
        "# 系統硬性風控（程式強制執行）\n" + limits_text,
    ])


def run_team(no_trade: bool = False, packet: MarketPacket | None = None,
             llm: TeamLLM | None = None, now: datetime | None = None) -> dict:
    """Run one cycle. `packet`/`llm` can be injected for tests."""
    now = now or datetime.now()
    print(f"\n{'='*64}\n  美股 AI 投資團隊啟動  {now:%Y-%m-%d %H:%M:%S}\n{'='*64}")

    llm = llm or TeamLLM()
    pf = load_portfolio()

    # ---------------- 1. Data + marking ----------------
    if packet is None:
        packet = collect_market_data(WATCHLIST, extra_symbols=list(pf.positions))
    prices = packet.prices
    stop_fills = []
    if not no_trade:
        stop_fills = pf.check_stops(prices, when=now)
        for t in stop_fills:
            print(f"  [停損] 賣出 {t.symbol} {t.shares:g} 股 @ {t.price:.2f}，實現 {t.realized_pnl:+.2f}")
    equity_before = pf.mark(prices, when=now)
    pf.save(portfolio_path())

    goal = evaluate_goal(equity_before, GOAL, pf.start_date, as_of=now.date())
    pf_summary = pf.summary(prices)

    valuations = batch_estimate(packet.fundamentals, prices)
    valuation_skipped = [s for s in packet.fundamentals if s not in valuations]
    valuation_text = describe_valuations(valuations, skipped=valuation_skipped)

    shared = _shared_context(packet, describe_portfolio(pf_summary), describe_goal(goal),
                             describe_limits(RISK), valuation_text)
    print(f"  [組合] 權益 ${equity_before:,.2f}｜回撤 {pf_summary['drawdown_pct']:.1f}%｜目標狀態 {goal.status}")
    print(f"  [估值] {len(valuations)}/{len(packet.fundamentals)} 檔完成三情境估值")

    # ---------------- 2. Analysts in parallel ----------------
    print(f"  [團隊] 四位分析師並行分析中 ({llm.model})...")
    analyst_reports: dict[str, str] = {}

    def _run(role):
        return role.key, llm.ask_text(
            role.title, shared, role.system, role.task,
            effort=MODEL.analyst_effort,
            web_search=role.web_search and MODEL.web_search,
            web_search_max_uses=MODEL.web_search_max_uses,
            print_stream=False,
        )

    with ThreadPoolExecutor(max_workers=len(ANALYSTS)) as ex:
        for key, text in ex.map(_run, ANALYSTS):
            analyst_reports[key] = text
            title = next(r.title for r in ANALYSTS if r.key == key)
            print(f"\n──── {title} ────\n{text}\n")

    analyst_block = "\n\n".join(
        f"## {r.title}\n{analyst_reports[r.key]}" for r in ANALYSTS
    )

    # ---------------- 3. Debate ----------------
    print("──── 多方研究員 ────")
    bull = llm.ask_text(BULL.title, shared, BULL.system,
                        f"# 分析師報告\n{analyst_block}\n\n# 任務\n{BULL.task}",
                        effort=MODEL.analyst_effort)
    print("──── 空方研究員 ────")
    bear = llm.ask_text(BEAR.title, shared, BEAR.system,
                        f"# 分析師報告\n{analyst_block}\n\n# 多方方案\n{bull}\n\n# 任務\n{BEAR.task}",
                        effort=MODEL.analyst_effort)

    # ---------------- 4. Risk manager ----------------
    print("──── 風險長裁決 ────")
    verdict: RiskVerdict = llm.ask_structured(
        RISK_ROLE.title, shared, RISK_ROLE.system,
        f"# 分析師報告\n{analyst_block}\n\n# 多方方案\n{bull}\n\n# 空方反駁\n{bear}\n\n# 任務\n{RISK_ROLE.task}",
        RiskVerdict, effort=MODEL.pm_effort,
    )
    print(f"  核准: {verdict.approved}｜風險分數 {verdict.overall_risk_score}/10｜"
          f"最大曝險 {verdict.max_new_exposure_pct:.0f}%｜否決 {verdict.vetoed_symbols or '無'}")
    print(f"  {verdict.required_changes}")

    # ---------------- 5. PM decision ----------------
    print("──── 投資組合經理決策 ────")
    decision: PMDecision = llm.ask_structured(
        PM.title, shared, PM.system,
        "\n\n".join([
            f"# 分析師報告\n{analyst_block}",
            f"# 多方方案\n{bull}",
            f"# 空方反駁\n{bear}",
            "# 風險長裁決\n" + verdict.model_dump_json(indent=2),
            f"# 任務\n{PM.task}",
        ]),
        PMDecision, effort=MODEL.pm_effort,
    )
    print(f"  立場: {decision.market_stance}\n  {decision.summary}")
    for o in decision.orders:
        print(f"  - {o.action} {o.symbol} → {o.target_weight_pct:.0f}%｜停損 {o.stop_loss_pct}｜信心 {o.confidence}")

    # ---------------- 6. Hard risk engine + execution ----------------
    universe = list(dict.fromkeys([*WATCHLIST, *pf.positions]))
    risk_report: RiskReport = plan_orders(decision, verdict, pf, prices, RISK, universe)
    print("──── 風控引擎 ────\n" + risk_report.describe())
    if no_trade:
        print("  [模式] --no-trade：不執行任何成交")
    else:
        apply_fills(risk_report, pf, when=now)
    equity_after = pf.mark(prices, when=now)
    pf.save(portfolio_path())
    goal_after = evaluate_goal(equity_after, GOAL, pf.start_date, as_of=now.date())

    print(f"\n  [完成] {llm.usage.calls} 次呼叫｜輸入 {llm.usage.input_tokens:,}｜輸出 {llm.usage.output_tokens:,}"
          f"｜快取讀取 {llm.usage.cache_read:,}｜搜尋 {llm.usage.web_searches}")

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "model": llm.model,
        "no_trade": no_trade,
        "goal": goal_after.to_dict(),
        "goal_text": describe_goal(goal_after),
        "milestones": milestones(GOAL, pf.start_date),
        "portfolio": pf.summary(prices),
        "valuations": {sym: v.to_dict() for sym, v in valuations.items()},
        "valuation_text": valuation_text,
        "stop_fills": [t.__dict__ for t in stop_fills],
        "analysts": {r.key: {"title": r.title, "report": analyst_reports[r.key]} for r in ANALYSTS},
        "bull": bull,
        "bear": bear,
        "risk_verdict": verdict.model_dump(),
        "decision": decision.model_dump(),
        "risk_engine": {
            "halted": risk_report.halted,
            "fills": [f.__dict__ for f in risk_report.fills],
            "adjusted": risk_report.adjusted,
            "rejected": risk_report.rejected,
            "text": risk_report.describe(),
        },
        "usage": llm.usage.to_dict(),
        "data_errors": packet.errors,
        "raw_packet": packet.to_dict(),
    }
