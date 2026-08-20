"""
投資團隊討論引擎 — 兩輪制（各自發言 → 交叉質詢），輸出結構化 JSON 評分。

流程：
    1. 抓取指定標的的行情 / 技術面 / 基本面，組成一份共用資料包
    2. Round 1：5 位分析師各自獨立發言（平行呼叫）
    3. Round 2：每人看過其他 4 人的發言後交叉質詢，並可修正自己的立場
    4. 以完全平權方式加總最終評分，算出團隊共識與分歧度
    5. 產出給主理人（使用者本人）的決策建議書 JSON

主理人不由 AI 擔任 — 本引擎只到「建議」為止，拍板由使用者自己來。
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import anthropic

from analyzer import analyze_symbol
from fetcher import (
    SECTOR_MAP, fetch_market_breadth, fetch_stock_info,
    fetch_taiex_summary, fetch_yfinance_data,
)
from personas import (
    ANALYSTS, ANALYSTS_BY_ID, DISCLAIMER, VOTE_WEIGHTS,
    build_persona_system_prompt,
)

MODEL = "claude-opus-5"
MAX_TOKENS = 16000
EFFORT = os.environ.get("TEAM_EFFORT", "high")  # low | medium | high | xhigh | max

STANCE_VALUE = {"BUY": 1.0, "HOLD": 0.0, "SELL": -1.0}
CONSENSUS_THRESHOLD = 0.25  # 團隊分數超過 ±0.25 才算方向明確

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


# --------------------------------------------------------------------------
# 結構化輸出 schema
# --------------------------------------------------------------------------

def _call_schema(extra_props: dict | None = None) -> dict:
    """單一標的的評分結構。extra_props 用於 Round 2 的修正欄位。"""
    props = {
        "symbol": {"type": "string", "description": "股票代號，需與資料包一致"},
        "stance": {"type": "string", "enum": ["BUY", "HOLD", "SELL"]},
        "conviction": {
            "type": "integer", "minimum": 0, "maximum": 10,
            "description": "信心度 0-10；資料不足時必須下修",
        },
        "thesis": {"type": "string", "description": "一段話講清楚你的核心論點"},
        "key_evidence": {"type": "array", "items": {"type": "string"}},
        "key_risks": {"type": "array", "items": {"type": "string"}},
        "target_price": {"type": ["number", "null"]},
        "stop_loss": {"type": ["number", "null"]},
        "time_horizon": {"type": "string", "description": "例如：3-6 個月、1-2 週、3 年以上"},
        "data_gaps": {"type": "string", "description": "你希望有但資料包沒給的資訊；沒有就寫「無」"},
    }
    if extra_props:
        props.update(extra_props)
    return {
        "type": "object",
        "properties": props,
        "required": list(props.keys()),
        "additionalProperties": False,
    }


ROUND1_SCHEMA = {
    "type": "object",
    "properties": {
        "market_view": {"type": "string", "description": "你對當前大盤環境的定調"},
        "calls": {"type": "array", "items": _call_schema()},
        "self_check": {"type": "string", "description": "依你已知的盲點，自評本次判斷可能偏在哪一邊"},
    },
    "required": ["market_view", "calls", "self_check"],
    "additionalProperties": False,
}

ROUND2_SCHEMA = {
    "type": "object",
    "properties": {
        "challenges": {
            "type": "array",
            "description": "對其他分析師的質詢，至少提出一則",
            "items": {
                "type": "object",
                "properties": {
                    "target_analyst": {
                        "type": "string",
                        "enum": [a["id"] for a in ANALYSTS],
                    },
                    "symbol": {"type": "string"},
                    "critique": {"type": "string", "description": "具體指出他哪個推論站不住腳"},
                },
                "required": ["target_analyst", "symbol", "critique"],
                "additionalProperties": False,
            },
        },
        "response_to_critics": {"type": "string", "description": "回應別人可能對你的質疑"},
        "revised_calls": {
            "type": "array",
            "description": "你的最終評分，需涵蓋資料包中的每一檔標的",
            "items": _call_schema({
                "changed": {"type": "boolean", "description": "相較第一輪是否有修正"},
                "change_reason": {"type": "string", "description": "有修正就說明被誰說服；沒改就寫「維持原判」"},
            }),
        },
    },
    "required": ["challenges", "response_to_critics", "revised_calls"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# 資料包
# --------------------------------------------------------------------------

def build_market_packet(symbols: list[str], period: str = "1mo",
                        interval: str = "1d") -> dict:
    """抓取指定標的的行情資料，組成一份所有分析師共用的資料包。"""
    print(f"  [資料] 抓取 {len(symbols)} 檔標的（period={period}, interval={interval}）...")
    ohlcv = fetch_yfinance_data(symbols, period=period, interval=interval)

    print("  [分析] 計算技術指標...")
    technicals = {sym: analyze_symbol(ohlcv.get(sym), sym) for sym in symbols}

    print("  [資料] 抓取個股基本面...")
    fundamentals = {sym: fetch_stock_info(sym) for sym in symbols}

    print("  [資料] 抓取大盤與市場寬度...")
    return {
        "symbols": symbols,
        "taiex": fetch_taiex_summary(),
        "market_breadth": fetch_market_breadth(),
        "technicals": technicals,
        "fundamentals": fundamentals,
        "period": period,
        "interval": interval,
        "collected_at": datetime.now().isoformat(),
    }


def _fmt(value, suffix: str = "") -> str:
    if value is None:
        return "無資料"
    if isinstance(value, float):
        return f"{value:,.2f}{suffix}"
    return f"{value}{suffix}"


def format_packet(packet: dict) -> str:
    """把資料包轉成給分析師閱讀的文字。"""
    taiex = packet["taiex"]
    breadth = packet["market_breadth"]

    lines = [
        "# 市場資料包",
        f"資料時間：{packet['collected_at']}",
        f"取樣區間：{packet['period']} / K 棒間隔：{packet['interval']}",
        "",
        "## 大盤 (TAIEX)",
        f"- 加權指數：{_fmt(taiex.get('taiex'))}",
        f"- 漲跌點數：{_fmt(taiex.get('change'))}",
        f"- 成交金額：{_fmt(taiex.get('volume_amount'))}",
        f"- 資料日期：{_fmt(taiex.get('date'))}",
        "",
        "## 市場寬度",
        f"- 上漲家數：{_fmt(breadth.get('up'))}｜下跌家數：{_fmt(breadth.get('down'))}"
        f"｜平盤：{_fmt(breadth.get('unchanged'))}",
        "",
        "## 本次討論標的",
    ]

    for sym in packet["symbols"]:
        t = packet["technicals"].get(sym, {})
        f = packet["fundamentals"].get(sym, {})
        sector = SECTOR_MAP.get(sym, "指數" if sym.startswith("^") else f.get("sector", "未知"))
        lines += ["", f"### {sym} — {f.get('name', sym)}（{sector}）"]

        if t.get("error"):
            lines.append(f"- 技術面：{t['error']}（請據此下修信心度）")
        else:
            lines += [
                f"- 最新價：{_fmt(t.get('last_price'))}｜漲跌：{_fmt(t.get('change_pct'), '%')}",
                f"- 趨勢：{_fmt(t.get('trend'))}｜RSI：{_fmt(t.get('rsi'))}｜量比：{_fmt(t.get('volume_ratio'), 'x')}",
                f"- MACD：{_fmt(t.get('macd'))}／訊號線：{_fmt(t.get('macd_signal'))}／柱狀：{_fmt(t.get('macd_hist'))}",
                f"- 布林：上 {_fmt(t.get('bb_upper'))}／中 {_fmt(t.get('bb_mid'))}／下 {_fmt(t.get('bb_lower'))}",
                f"- 均線：MA5 {_fmt(t.get('sma5'))}｜MA20 {_fmt(t.get('sma20'))}",
                f"- 區間高低：{_fmt(t.get('period_high'))} / {_fmt(t.get('period_low'))}",
                f"- 技術訊號：{'、'.join(t.get('signals', [])) or '無特殊訊號'}",
            ]

        if f.get("error"):
            lines.append(f"- 基本面：抓取失敗（{f['error']}）")
        else:
            lines += [
                f"- 市值：{_fmt(f.get('market_cap'))}｜本益比：{_fmt(f.get('pe_ratio'))}",
                f"- 52 週高／低：{_fmt(f.get('52w_high'))} / {_fmt(f.get('52w_low'))}",
                f"- 均量：{_fmt(f.get('avg_volume'))}",
            ]

    lines += [
        "",
        "## 資料限制（請務必納入信心度評估）",
        "- 本資料包只含價量技術指標與 yfinance 基本面欄位，沒有財報全文、法說會、"
        "產業訪查、外資報告或籌碼細節。",
        "- 需要而未取得的資訊，請寫進 data_gaps，不要自行推測數字。",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# LLM 呼叫
# --------------------------------------------------------------------------

def _ask(analyst: dict, messages: list, schema: dict) -> tuple[dict, dict]:
    """對單一分析師發出一次結構化請求，回傳 (解析後的 JSON, usage)。"""
    client = _get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=build_persona_system_prompt(analyst),
        messages=messages,
        thinking={"type": "adaptive"},
        output_config={
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": schema},
        },
        cache_control={"type": "ephemeral"},
    )
    text = next(b.text for b in response.content if b.type == "text")
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read": response.usage.cache_read_input_tokens,
        "cache_created": response.usage.cache_creation_input_tokens,
    }
    return json.loads(text), usage


def _round1_prompt(packet_text: str) -> str:
    return "\n".join([
        packet_text,
        "",
        "# 第一輪：獨立發言",
        "在還沒看到其他分析師意見的情況下，請對上述每一檔標的給出你的判斷。",
        "要求：",
        "1. 先定調你眼中的大盤環境（market_view）。",
        "2. 對每一檔標的給出 stance（BUY/HOLD/SELL）與 conviction（0-10）。",
        "3. thesis 用你自己的語言講，evidence 必須指向資料包裡的具體數字。",
        "4. 你的學派用不到的指標就不要硬掰；資料不足直接反映在 conviction 與 data_gaps。",
        "5. self_check 誠實評估你的已知盲點這次可能讓你偏向哪一邊。",
    ])


def _round2_prompt(peer_notes: str) -> str:
    return "\n".join([
        "# 第二輪：交叉質詢",
        "以下是其他四位分析師的第一輪發言：",
        "",
        peer_notes,
        "",
        "請你：",
        "1. 至少提出一則具體質詢（challenges），指名對象與標的，"
        "針對推論本身，不要只是說『我的學派不同意』。",
        "2. 回應你預期別人會對你提出的質疑（response_to_critics）。",
        "3. 給出最終評分（revised_calls），必須涵蓋資料包中每一檔標的。"
        "被說服就改，並在 change_reason 說明被誰的哪個論點說服；沒被說服就維持原判並說明為什麼。",
        "4. 立場不變也要重新確認 conviction — 看過反方論點後信心度本來就可能微調。",
    ])


def _format_peer_notes(round1: dict, exclude_id: str) -> str:
    blocks = []
    for aid, result in round1.items():
        if aid == exclude_id or "error" in result:
            continue
        a = ANALYSTS_BY_ID[aid]
        blocks.append(f"## {a['name']}（{a['school']}）")
        blocks.append(f"大盤定調：{result['market_view']}")
        for c in result["calls"]:
            blocks.append(
                f"- {c['symbol']}：{c['stance']}（信心 {c['conviction']}/10，"
                f"{c['time_horizon']}）— {c['thesis']}"
            )
            if c.get("key_evidence"):
                blocks.append(f"  依據：{'；'.join(c['key_evidence'])}")
            if c.get("key_risks"):
                blocks.append(f"  風險：{'；'.join(c['key_risks'])}")
        blocks.append("")
    return "\n".join(blocks)


# --------------------------------------------------------------------------
# 平權加總
# --------------------------------------------------------------------------

def _signed_score(call: dict) -> float:
    """把單一評分換算成 -1.0 ~ +1.0 的帶號分數。"""
    return STANCE_VALUE.get(call["stance"], 0.0) * (call["conviction"] / 10.0)


def aggregate(final_calls: dict, symbols: list[str]) -> dict:
    """
    完全平權加總：五人一人一票，以信心度加權後取平均。
    回傳每檔標的的團隊共識、分歧度與個別立場。
    """
    consensus = {}

    for sym in symbols:
        entries = []
        for aid, result in final_calls.items():
            if "error" in result:
                continue
            for c in result["revised_calls"]:
                if c["symbol"] == sym:
                    entries.append((aid, c))
                    break

        if not entries:
            consensus[sym] = {"error": "沒有任何分析師對此標的給出評分"}
            continue

        scores = [_signed_score(c) for _, c in entries]
        team_score = sum(s * VOTE_WEIGHTS[aid] for s, (aid, _) in zip(scores, entries))
        team_score /= sum(VOTE_WEIGHTS[aid] for aid, _ in entries)

        votes = {"BUY": 0, "HOLD": 0, "SELL": 0}
        for _, c in entries:
            votes[c["stance"]] += 1

        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        dispersion = variance ** 0.5

        if team_score >= CONSENSUS_THRESHOLD:
            stance = "BUY"
        elif team_score <= -CONSENSUS_THRESHOLD:
            stance = "SELL"
        else:
            stance = "HOLD"

        split = votes["BUY"] > 0 and votes["SELL"] > 0
        targets = [c["target_price"] for _, c in entries if c.get("target_price") is not None]
        stops = [c["stop_loss"] for _, c in entries if c.get("stop_loss") is not None]

        consensus[sym] = {
            "team_stance": stance,
            "team_score": round(team_score, 3),
            "votes": votes,
            "dispersion": round(dispersion, 3),
            "is_split": split,
            "needs_manual_review": split or dispersion >= 0.5,
            "avg_conviction": round(sum(c["conviction"] for _, c in entries) / len(entries), 1),
            "target_price_range": (
                {"low": min(targets), "high": max(targets),
                 "median": sorted(targets)[len(targets) // 2]}
                if targets else None
            ),
            "tightest_stop_loss": max(stops) if stops else None,
            "positions": [
                {
                    "analyst": aid,
                    "name": ANALYSTS_BY_ID[aid]["name"],
                    "school": ANALYSTS_BY_ID[aid]["school"],
                    "stance": c["stance"],
                    "conviction": c["conviction"],
                    "signed_score": round(_signed_score(c), 3),
                    "thesis": c["thesis"],
                    "key_risks": c["key_risks"],
                    "target_price": c["target_price"],
                    "stop_loss": c["stop_loss"],
                    "time_horizon": c["time_horizon"],
                    "changed_in_round2": c.get("changed", False),
                    "change_reason": c.get("change_reason", ""),
                    "data_gaps": c["data_gaps"],
                }
                for aid, c in entries
            ],
        }

    return consensus


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def _run_parallel(fn, items: list) -> dict:
    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        return dict(pool.map(fn, items))


def run_discussion(symbols: list[str], period: str = "1mo",
                   interval: str = "1d", cross_examine: bool = True) -> dict:
    """跑完一場完整的投資團隊討論，回傳結構化結果。"""
    started = datetime.now()
    print(f"\n{'=' * 64}")
    print(f"  投資團隊會議  {started.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  主理人：使用者本人｜分析師：{len(ANALYSTS)} 位（完全平權）")
    print(f"  標的：{', '.join(symbols)}")
    print(f"{'=' * 64}")

    packet = build_market_packet(symbols, period=period, interval=interval)
    packet_text = format_packet(packet)
    usages: list[dict] = []

    # ---- Round 1：各自獨立發言 ----
    print(f"\n  [R1] 五位分析師獨立發言中（平行呼叫，model={MODEL}, effort={EFFORT}）...")
    histories: dict[str, list] = {}

    def _r1(analyst: dict):
        prompt = _round1_prompt(packet_text)
        try:
            result, usage = _ask(analyst, [{"role": "user", "content": prompt}], ROUND1_SCHEMA)
            usages.append(usage)
            histories[analyst["id"]] = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)},
            ]
            sys.stdout.write(f"       ✓ {analyst['name']} 發言完成\n")
            return analyst["id"], result
        except Exception as e:
            sys.stdout.write(f"       ✗ {analyst['name']} 失敗 — {e}\n")
            return analyst["id"], {"error": str(e)}

    round1 = _run_parallel(_r1, ANALYSTS)

    if not cross_examine:
        final = {
            aid: ({"error": r["error"]} if "error" in r
                  else {"challenges": [], "response_to_critics": "",
                        "revised_calls": [dict(c, changed=False, change_reason="未進行第二輪")
                                          for c in r["calls"]]})
            for aid, r in round1.items()
        }
        round2 = {}
    else:
        # ---- Round 2：交叉質詢 ----
        print("\n  [R2] 交叉質詢與立場修正中...")

        def _r2(analyst: dict):
            aid = analyst["id"]
            if aid not in histories:
                return aid, {"error": "第一輪失敗，跳過第二輪"}
            prompt = _round2_prompt(_format_peer_notes(round1, aid))
            try:
                result, usage = _ask(
                    analyst, histories[aid] + [{"role": "user", "content": prompt}],
                    ROUND2_SCHEMA,
                )
                usages.append(usage)
                changed = sum(1 for c in result["revised_calls"] if c.get("changed"))
                sys.stdout.write(f"       ✓ {analyst['name']} 質詢 "
                                 f"{len(result['challenges'])} 則，修正 {changed} 檔\n")
                return aid, result
            except Exception as e:
                sys.stdout.write(f"       ✗ {analyst['name']} 失敗 — {e}\n")
                return aid, {"error": str(e)}

        round2 = _run_parallel(_r2, ANALYSTS)
        final = round2

    # ---- 平權加總 ----
    print("\n  [彙總] 完全平權加總五人評分...")
    consensus = aggregate(final, symbols)

    for sym, c in consensus.items():
        if "error" in c:
            print(f"       {sym}: {c['error']}")
            continue
        flag = "  ⚠ 分歧大，建議人工複核" if c["needs_manual_review"] else ""
        print(f"       {sym}: {c['team_stance']}（分數 {c['team_score']:+.2f}，"
              f"票數 B{c['votes']['BUY']}/H{c['votes']['HOLD']}/S{c['votes']['SELL']}）{flag}")

    total_usage = {
        "api_calls": len(usages),
        "input_tokens": sum(u["input_tokens"] for u in usages),
        "output_tokens": sum(u["output_tokens"] for u in usages),
        "cache_read": sum(u["cache_read"] for u in usages),
        "cache_created": sum(u["cache_created"] for u in usages),
    }
    print(f"\n  [完成] 耗時 {(datetime.now() - started).total_seconds():.0f}s｜"
          f"API 呼叫 {total_usage['api_calls']} 次｜"
          f"輸入 {total_usage['input_tokens']} / 輸出 {total_usage['output_tokens']} tokens｜"
          f"快取讀取 {total_usage['cache_read']}")

    return {
        "meeting_time": started.isoformat(),
        "symbols": symbols,
        "moderator": "使用者本人（AI 不代為拍板）",
        "voting_rule": "完全平權：五人一人一票，以信心度加權後取平均",
        "rounds": ["opening", "cross_examination"] if cross_examine else ["opening"],
        "model": MODEL,
        "effort": EFFORT,
        "analysts": [
            {"id": a["id"], "name": a["name"], "school": a["school"]} for a in ANALYSTS
        ],
        "consensus": consensus,
        "round1": round1,
        "round2": round2,
        "market_packet": packet,
        "usage": total_usage,
        "disclaimer": DISCLAIMER,
    }
