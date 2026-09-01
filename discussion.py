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
import statistics
from datetime import datetime

from analyzer import analyze_symbol
from fetcher import (
    SECTOR_MAP, fetch_market_breadth, fetch_stock_info,
    fetch_taiex_summary, fetch_yfinance_data,
)
import backends
import gates
import lens
import payoff
from backends import ask_many, describe as describe_backend, preflight
from personas import (
    ANALYSTS, ANALYSTS_BY_ID, DISCLAIMER, VOTE_WEIGHTS,
    build_persona_system_prompt,
)

STANCE_VALUE = {"BUY": 1.0, "HOLD": 0.0, "SELL": -1.0}
# 團隊分數的單位是 R（風險倍數）：+0.3R 代表每承擔 1 單位風險，團隊預期賺 0.3
CONSENSUS_THRESHOLD = 0.30

# --------------------------------------------------------------------------
# 結構化輸出 schema
# --------------------------------------------------------------------------

# 輸出長度上限。模型不受限時會把每個欄位寫成長篇，且同一批來源在兩輪重複輸出，
# 實測一場會議 56K 字元中 23% 是 web_sources、15% 是 challenges。
# 上限訂在「足以承載完整論證」而非「逼出摘要」，論點品質不變、贅字消失。
LIMITS = {
    "thesis": 500,          # 核心論點：一段話講完
    "evidence_item": 120,   # 單條證據
    "evidence_items": 4,
    "risk_item": 120,
    "risk_items": 4,
    "source_item": 90,      # 「媒體·標題·日期」，不含 percent-encoded 長網址
    "source_items": 4,
    "data_gaps": 200,
    "change_reason": 350,
    "critique": 300,
    "challenges": 2,
    "response": 400,
    "market_view": 300,
    "self_check": 250,
}


def _call_schema(extra_props: dict | None = None) -> dict:
    """單一標的的評分結構。extra_props 用於 Round 2 的修正欄位。"""
    props = {
        "symbol": {"type": "string", "description": "股票代號，需與資料包一致"},
        "web_sources": {
            "type": "array",
            "items": {"type": "string", "maxLength": LIMITS["source_item"]},
            "maxItems": LIMITS["source_items"],
            "description": "實際用來支持論點的來源，格式「媒體·標題重點·日期」，"
                          "不要貼完整網址（percent-encoded 網址極耗篇幅）。"
                          "沒查到就給空陣列，不可編造",
        },
        "stance": {"type": "string", "enum": ["BUY", "HOLD", "SELL"]},
        "p_target": {
            "type": ["number", "null"], "minimum": 0, "maximum": 1,
            "description": "在本次會議的時間框架內，目標價「先於」停損被觸及的機率（0-1）。"
                          "這是會被 Brier 分數事後打分的真機率，不是感覺；"
                          "HOLD 或沒給目標／停損時填 null",
        },
        "horizon_fit": {
            "type": "boolean",
            "description": "本次會議的時間框架是否落在你這個學派有判斷力的範圍內。"
                          "誠實填 false 會讓你被排除計票 —— 在不擅長的期間硬表態"
                          "只會增加雜訊，不會增加資訊",
        },
        "thesis": {"type": "string", "maxLength": LIMITS["thesis"],
                   "description": "一段話講清楚你的核心論點，直接講結論與理由，不要鋪陳"},
        "key_evidence": {
            "type": "array",
            "items": {"type": "string", "maxLength": LIMITS["evidence_item"]},
            "maxItems": LIMITS["evidence_items"],
            "description": "每條一個具體數字或事實，不要整段論述",
        },
        "key_risks": {
            "type": "array",
            "items": {"type": "string", "maxLength": LIMITS["risk_item"]},
            "maxItems": LIMITS["risk_items"],
        },
        "target_price": {"type": ["number", "null"]},
        "stop_loss": {"type": ["number", "null"]},
        "data_gaps": {"type": "string", "maxLength": LIMITS["data_gaps"],
                      "description": "希望有但沒拿到的資訊，條列關鍵字即可；沒有就寫「無」"},
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
        "market_view": {"type": "string", "maxLength": LIMITS["market_view"],
                        "description": "你對當前大盤環境的定調"},
        "calls": {"type": "array", "items": _call_schema()},
        "self_check": {"type": "string", "maxLength": LIMITS["self_check"],
                       "description": "依你已知的盲點，自評本次判斷可能偏在哪一邊"},
    },
    "required": ["market_view", "calls", "self_check"],
    "additionalProperties": False,
}

ROUND2_SCHEMA = {
    "type": "object",
    "properties": {
        "challenges": {
            "type": "array",
            "maxItems": LIMITS["challenges"],
            "description": "對其他分析師的質詢，至少一則、至多兩則，挑最要害的講",
            "items": {
                "type": "object",
                "properties": {
                    "target_analyst": {
                        "type": "string",
                        "enum": [a["id"] for a in ANALYSTS],
                    },
                    "symbol": {"type": "string"},
                    "critique": {"type": "string", "maxLength": LIMITS["critique"],
                                 "description": "直接指出他哪個推論站不住腳，不要鋪陳"},
                },
                "required": ["target_analyst", "symbol", "critique"],
                "additionalProperties": False,
            },
        },
        "response_to_critics": {"type": "string", "maxLength": LIMITS["response"],
                                "description": "回應別人可能對你的質疑"},
        "revised_calls": {
            "type": "array",
            "description": "你的最終評分，需涵蓋資料包中的每一檔標的",
            "items": _call_schema({
                "changed": {"type": "boolean", "description": "相較第一輪是否有修正"},
                "change_reason": {"type": "string", "maxLength": LIMITS["change_reason"],
                                  "description": "有修正就說明被誰說服；沒改就寫「維持原判」"},
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

    # 台股大盤資料只對台股有意義，非台股標的不必浪費兩次網路呼叫
    has_tw = any(lens.is_taiwan_symbol(s) for s in symbols)
    if has_tw:
        print("  [資料] 抓取大盤與市場寬度...")
    return {
        "symbols": symbols,
        "taiex": fetch_taiex_summary() if has_tw else {},
        "market_breadth": fetch_market_breadth() if has_tw else {},
        "technicals": technicals,
        "fundamentals": fundamentals,
        "period": period,
        "interval": interval,
        "collected_at": datetime.now().isoformat(),
    }


_fmt = lens._fmt  # 呈現格式由 lens 統一擁有，避免兩處各寫一份


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
                f"- 區間高低：{_fmt(t.get('period_high'))} / {_fmt(t.get('period_low'))}"
                f"（距高點 {_fmt(t.get('dist_from_high_pct'), '%')}、"
                f"距低點 {_fmt(t.get('dist_from_low_pct'), '%')}）",
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
# 提示詞
# --------------------------------------------------------------------------

def _horizon_block(days: int) -> str:
    return "\n".join([
        f"# 本次會議的時間框架：{days} 天",
        f"所有判斷都必須針對「未來 {days} 天內」這個窗口。五個人回答同一個問題，",
        "答案才能被加總 —— 把兩週的看法和三年的看法平均起來在數學上沒有意義。",
        "若這個窗口不在你這個學派有判斷力的範圍內，horizon_fit 誠實填 false，",
        "你會被排除計票；在不擅長的期間硬表態只會增加雜訊，不會增加資訊。",
        "",
        "目標價與停損必須成對給出，並估計 p_target（目標先於停損被觸及的機率）。",
        "這個機率事後會用 Brier 分數打分，估不準會被記錄下來。",
        "",
    ])


def _round1_prompt(packet_text: str, web_search_enabled: bool = True,
                   horizon_days: int = 90) -> str:
    lines = [
        packet_text,
        "",
        _horizon_block(horizon_days),
        "# 第一輪：獨立發言",
        "在還沒看到其他分析師意見的情況下，請對上述每一檔標的給出你的判斷。",
        "要求：",
        "1. 先定調你眼中的大盤環境（market_view）。",
    ]
    if web_search_enabled:
        lines.append("2. 需要即時資訊時先查證，再給出 stance、目標價、停損與 p_target。")
    else:
        lines.append("2. 對每一檔標的給出 stance、目標價、停損與 p_target。")
    lines += [
        "3. thesis 用你自己的語言講，evidence 必須指向資料包裡的具體數字"
        + ("或你查到的具體來源" if web_search_enabled else "") + "。",
        "4. 你的學派用不到的指標就不要硬掰；資料不足直接反映在 conviction 與 data_gaps。",
        "5. self_check 誠實評估你的已知盲點這次可能讓你偏向哪一邊。",
        "6. 賠率低於 2:1 的機會不值得動用資金 —— 與其湊一個勉強的目標價，"
        "不如誠實給 HOLD。",
    ]
    return "\n".join(lines)


def _round2_prompt(peer_notes: str, web_search_enabled: bool = True) -> str:
    lines = [
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
        "4. 立場不變也要重新確認 p_target — 看過反方論點後機率估計本來就可能微調。",
    ]
    if web_search_enabled:
        lines.append("5. 質詢或回應時如果需要更多即時資訊佐證，可以再查一次。"
                     "web_sources 只填這一輪「新查到」的來源，第一輪已經給過的不要重貼"
                     "（系統會自動合併兩輪來源）。")
    return "\n".join(lines)


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
                f"- {c['symbol']}：{c['stance']}"
                f"（目標達成機率 {c.get('p_target')}）— {c['thesis']}"
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

def _payoff_of(call: dict) -> dict:
    """把一則評分換算成賠率與期望值（單位：R）。算術一律由 Python 做。"""
    return payoff.assess({
        "stance": call["stance"],
        "entry": call.get("entry"),
        "stop": call.get("stop_loss"),
        "target": call.get("target_price"),
        "p_target": call.get("p_target"),
    })


def _norm_symbol(value) -> str:
    return str(value).strip().upper()


def _find_call(calls: list, symbol: str, all_symbols: list[str]) -> dict | None:
    """
    在一位分析師的評分裡找出對應標的。

    模型偶爾會把 3037.TW 寫成 3037，因此在不會與其他標的混淆的前提下，
    允許比對「.」之前的代號主體 —— 找不到就回 None，由呼叫端記錄為未表態，
    絕不可以靜默略過而讓平權的分母悄悄變小。
    """
    target = _norm_symbol(symbol)
    for c in calls:
        if _norm_symbol(c.get("symbol")) == target:
            return c

    base = target.split(".")[0]
    if sum(1 for s in all_symbols if _norm_symbol(s).split(".")[0] == base) == 1:
        for c in calls:
            if _norm_symbol(c.get("symbol")).split(".")[0] == base:
                return c
    return None


def _sanitize_call(call: dict) -> tuple[dict | None, str | None]:
    """
    檢查並修正單一評分。

    結構化輸出正常時這裡不會攔到東西，但 CLI 後端在拿不到 structured_output
    時會退回自行解析 JSON，那條路徑沒有 schema 把關 —— 立場拼錯會讓計票
    直接崩潰，信心度超界會讓分數爆表，兩者都必須在這裡擋下。
    """
    stance = _norm_symbol(call.get("stance"))
    if stance not in STANCE_VALUE:
        return None, f"立場無法辨識（{call.get('stance')!r}）"

    p, note = call.get("p_target"), None
    if p is not None:
        try:
            p = float(p)
        except (TypeError, ValueError):
            return None, f"目標達成機率無法辨識（{call.get('p_target')!r}）"
        clamped = min(max(p, 0.0), 1.0)
        if clamped != p:
            note, p = f"機率 {p} 超出 0-1，已夾限為 {clamped}", clamped
    return dict(call, stance=stance, p_target=p), note


def _merge_sources(round1: dict, aid: str, symbol: str, revised: list) -> list:
    """合併分析師兩輪的來源 —— 第二輪只填新來源，第一輪的在這裡補回。"""
    seen, merged = set(), []
    r1 = round1.get(aid) or {}
    pools = [] if "error" in r1 else [r1.get("calls", [])]
    pools.append([{"symbol": symbol, "web_sources": revised}])
    for pool in pools:
        for c in pool:
            if _norm_symbol(c.get("symbol")) != _norm_symbol(symbol):
                continue
            for src in c.get("web_sources") or []:
                if src not in seen:
                    seen.add(src)
                    merged.append(src)
    return merged


def aggregate(final_calls: dict, symbols: list[str], round1: dict | None = None,
              packet: dict | None = None) -> dict:
    """
    完全平權加總：五人一人一票，以信心度加權後取平均。
    回傳每檔標的的團隊共識、分歧度與個別立場。
    """
    consensus = {}

    expected = [a["id"] for a in ANALYSTS]

    tech = (packet or {}).get("technicals", {})
    for sym in symbols:
        entry_price = (tech.get(sym) or {}).get("last_price")
        entries = []
        excluded = []
        warnings = []
        for aid in expected:
            result = final_calls.get(aid)
            if result is None:
                excluded.append((aid, "沒有回覆"))
                continue
            if "error" in result:
                excluded.append((aid, result["error"]))
                continue

            found = _find_call(result.get("revised_calls", []), sym, symbols)
            if found is None:
                excluded.append((aid, "回覆中沒有這檔標的的評分"))
                continue

            clean, note = _sanitize_call(found)
            if clean is None:
                excluded.append((aid, note))
                continue
            # 自認這個時間框架不在能力圈內 —— 在不擅長的期間硬表態只會增加雜訊
            if clean.get("horizon_fit") is False:
                excluded.append((aid, "自述本次時間框架不在其能力圈，排除計票"))
                continue
            clean["entry"] = entry_price
            if note:
                warnings.append(f"{ANALYSTS_BY_ID[aid]['name']}：{note}")
            entries.append((aid, clean))

        if not entries:
            consensus[sym] = {
                "error": "沒有任何分析師對此標的給出可用的評分",
                "excluded": [{"analyst": aid, "name": ANALYSTS_BY_ID[aid]["name"],
                              "reason": r} for aid, r in excluded],
            }
            continue

        payoffs = {aid: _payoff_of(c) for aid, c in entries}
        scores = [payoffs[aid]["signed_expected_r"] for aid, _ in entries]
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
            "voter_count": len(entries),
            "expected_voters": len(expected),
            "excluded": [{"analyst": aid, "name": ANALYSTS_BY_ID[aid]["name"],
                          "reason": r} for aid, r in excluded],
            "data_warnings": warnings,
            "dispersion": round(dispersion, 3),
            "is_split": split,
            "needs_manual_review": split or dispersion >= 0.5 or bool(excluded),
            "actionable_count": sum(1 for v in payoffs.values() if v["actionable"]),
            "avg_p_target": (
                round(sum(c["p_target"] for _, c in entries if c.get("p_target") is not None)
                      / max(1, sum(1 for _, c in entries if c.get("p_target") is not None)), 2)
                if any(c.get("p_target") is not None for _, c in entries) else None
            ),
            "target_price_range": (
                {"low": min(targets), "high": max(targets),
                 "median": statistics.median(targets)}
                if targets else None
            ),
            "tightest_stop_loss": max(stops) if stops else None,
            "positions": [
                {
                    "analyst": aid,
                    "name": ANALYSTS_BY_ID[aid]["name"],
                    "school": ANALYSTS_BY_ID[aid]["school"],
                    "stance": c["stance"],
                    "p_target": c.get("p_target"),
                    "r_target": payoffs[aid]["r_target"],
                    "expected_r": payoffs[aid]["expected_r"],
                    "signed_score": payoffs[aid]["signed_expected_r"],
                    "actionable": payoffs[aid]["actionable"],
                    "payoff_note": payoffs[aid]["reason"],
                    "thesis": c["thesis"],
                    "key_risks": c["key_risks"],
                    "target_price": c["target_price"],
                    "stop_loss": c["stop_loss"],
                    "changed_in_round2": c.get("changed", False),
                    "change_reason": c.get("change_reason", ""),
                    "data_gaps": c["data_gaps"],
                    "web_sources": (_merge_sources(round1 or {}, aid, sym,
                                                   c.get("web_sources", []))
                                    if round1 else c.get("web_sources", [])),
                }
                for aid, c in entries
            ],
        }

    return consensus


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def run_discussion(symbols: list[str], period: str = "1mo",
                   interval: str = "1d", cross_examine: bool = True,
                   packet: dict | None = None, horizon_days: int = 90) -> dict:
    """
    跑完一場完整的投資團隊討論，回傳結構化結果。

    packet 給定時就直接採用該資料包、不再抓行情 —— 供離線機器或回測使用
    （可用 team.py --dump-packet 在有網路的機器上先產出）。
    """
    preflight()

    if packet is not None:
        symbols = packet["symbols"]
    symbols = list(dict.fromkeys(symbols))  # 去重並保持順序，避免重複抓取與重複計票

    started = datetime.now()
    print(f"\n{'=' * 64}")
    print(f"  投資團隊會議  {started.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  主理人：使用者本人｜分析師：{len(ANALYSTS)} 位（完全平權）")
    print(f"  標的：{', '.join(symbols)}｜時間框架：{horizon_days} 天")
    print(f"  後端：{describe_backend()}")
    print(f"{'=' * 64}")

    if packet is None:
        packet = build_market_packet(symbols, period=period, interval=interval)
    else:
        print(f"  [資料] 使用既有資料包（擷取於 {packet.get('collected_at', '未知時間')}）")
    ok, why = gates.packet_is_usable(packet)
    if not ok:
        print(f"\n  [中止] {why}")
        return {
            "meeting_time": started.isoformat(), "symbols": symbols,
            "horizon_days": horizon_days,
        "lens_coverage": lens.coverage(), "aborted": why,
            "consensus": {s_: {"error": why} for s_ in symbols},
            "round1": {}, "round2": {}, "market_packet": packet,
            "usage": {"llm_calls": 0}, "disclaimer": DISCLAIMER,
        }

    usages: list[dict] = []

    # ---- Round 1：各自獨立發言 ----
    web_search = backends.WEB_SEARCH  # 執行當下才讀，不能在模組載入時就綁死
    print(f"\n  [R1] 五位分析師獨立發言中（平行呼叫{'，含網路搜尋' if web_search else ''}）...")
    # 每位分析師只拿符合其學派的資料切片：分歧才會來自證據差異而非人格差異
    prompts = {
        a["id"]: _round1_prompt(lens.for_analyst(packet, a["id"]),
                                web_search_enabled=web_search,
                                horizon_days=horizon_days)
        for a in ANALYSTS
    }
    replies = ask_many([
        {
            "key": a["id"],
            "label": a["name"],
            "system": build_persona_system_prompt(a, web_search_enabled=web_search),
            "messages": [{"role": "user", "content": prompts[a["id"]]}],
            "schema": ROUND1_SCHEMA,
        }
        for a in ANALYSTS
    ])

    round1: dict[str, dict] = {}
    histories: dict[str, list] = {}
    for aid, (data, usage, error) in replies.items():
        usages.append(usage)
        if error is not None:
            round1[aid] = {"error": str(error)}
            continue
        round1[aid] = data
        histories[aid] = [
            {"role": "user", "content": prompts[aid]},
            {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
        ]

    if cross_examine:
        contested, gate_reason = gates.needs_cross_exam(round1, symbols)
        if not contested:
            print(f"  [R2] 略過 —— {gate_reason}")
            cross_examine = False
        else:
            print(f"  [R2] {gate_reason}")

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
        pending = [a for a in ANALYSTS if a["id"] in histories]
        replies = ask_many([
            {
                "key": a["id"],
                "label": a["name"],
                "system": build_persona_system_prompt(a, web_search_enabled=web_search),
                "messages": histories[a["id"]] + [
                    {"role": "user", "content": _round2_prompt(
                        _format_peer_notes(round1, a["id"]), web_search_enabled=web_search)},
                ],
                "schema": ROUND2_SCHEMA,
            }
            for a in pending
        ]) if pending else {}

        round2 = {a["id"]: {"error": "第一輪失敗，跳過第二輪"}
                  for a in ANALYSTS if a["id"] not in histories}
        for aid, (data, usage, error) in replies.items():
            usages.append(usage)
            round2[aid] = {"error": str(error)} if error is not None else data
        final = round2

        for aid, r in round2.items():
            if "error" not in r:
                changed = sum(1 for c in r["revised_calls"] if c.get("changed"))
                print(f"       · {ANALYSTS_BY_ID[aid]['name']}："
                      f"質詢 {len(r['challenges'])} 則，修正 {changed} 檔")

    # ---- 平權加總 ----
    print("\n  [彙總] 完全平權加總五人評分...")
    consensus = aggregate(final, symbols, round1=round1, packet=packet)

    for sym, c in consensus.items():
        if "error" in c:
            print(f"       {sym}: {c['error']}")
            continue
        flags = []
        if c["voter_count"] < c["expected_voters"]:
            flags.append(f"僅 {c['voter_count']}/{c['expected_voters']} 人計入")
        if c["is_split"] or c["dispersion"] >= 0.5:
            flags.append("分歧大")
        flag = f"  ⚠ {'、'.join(flags)}，建議人工複核" if flags else ""
        print(f"       {sym}: {c['team_stance']}（期望值 {c['team_score']:+.2f}R，"
              f"票數 B{c['votes']['BUY']}/H{c['votes']['HOLD']}/S{c['votes']['SELL']}）{flag}")

    total_usage = {
        "llm_calls": len(usages),
        "input_tokens": sum(u["input_tokens"] for u in usages),
        "output_tokens": sum(u["output_tokens"] for u in usages),
        "cache_read": sum(u["cache_read"] for u in usages),
        "cache_created": sum(u["cache_created"] for u in usages),
        "cost_usd": round(sum(u.get("cost_usd", 0.0) for u in usages), 4),
    }
    print(f"\n  [完成] 耗時 {(datetime.now() - started).total_seconds():.0f}s｜"
          f"呼叫 {total_usage['llm_calls']} 次｜"
          f"輸入 {total_usage['input_tokens']} / 輸出 {total_usage['output_tokens']} tokens")

    return {
        "meeting_time": started.isoformat(),
        "symbols": symbols,
        "moderator": "使用者本人（AI 不代為拍板）",
        "voting_rule": "完全平權：五人一人一票，以信心度加權後取平均",
        "horizon_days": horizon_days,
        "lens_coverage": lens.coverage(),
        "rounds": ["opening", "cross_examination"] if cross_examine else ["opening"],
        "backend": describe_backend(),
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
