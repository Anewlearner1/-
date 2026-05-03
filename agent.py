"""Claude-powered Taiwan stock market analysis agent."""
import json
import os
from datetime import datetime
import anthropic

from fetcher import (
    fetch_taiex_summary, fetch_market_breadth,
    fetch_all_watchlist, fetch_stock_info, WATCH_LIST, SECTOR_MAP,
)
from analyzer import analyze_symbol, build_market_summary

MODEL = "claude-opus-4-7"
_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _collect_market_data() -> dict:
    """Gather all market data and run technical analysis."""
    print("  [資料] 抓取 TAIEX 大盤資訊...")
    taiex = fetch_taiex_summary()

    print("  [資料] 抓取市場寬度...")
    breadth = fetch_market_breadth()

    print("  [資料] 下載歷史 OHLCV (yfinance 5日, 1小時)...")
    ohlcv_data = fetch_all_watchlist(period="5d", interval="1h")

    print("  [分析] 計算技術指標...")
    analyses: dict[str, dict] = {}
    for sym in WATCH_LIST:
        df = ohlcv_data.get(sym)
        analyses[sym] = analyze_symbol(df, sym)

    market_summary = build_market_summary(analyses)

    # Enrich with fundamental info for a subset of stocks
    print("  [資料] 抓取個股基本面...")
    fundamentals: dict[str, dict] = {}
    key_stocks = ["^TWII", "2330.TW", "2454.TW", "2317.TW", "2882.TW"]
    for sym in key_stocks:
        fundamentals[sym] = fetch_stock_info(sym)

    return {
        "taiex": taiex,
        "market_breadth": breadth,
        "technical_analyses": analyses,
        "market_summary": market_summary,
        "fundamentals": fundamentals,
        "analysis_time": datetime.now().isoformat(),
    }


def _build_prompt(data: dict) -> str:
    """Build a structured prompt from collected market data."""
    ta = data["technical_analyses"]
    ms = data["market_summary"]
    taiex = data["taiex"]

    lines = [
        "以下是台灣股市的即時技術分析資料，請提供專業的中文市場分析報告。",
        "",
        "# 大盤 (TAIEX) 狀態",
    ]

    if taiex.get("taiex"):
        lines += [
            f"- 加權指數: {taiex.get('taiex')}",
            f"- 漲跌點數: {taiex.get('change')}",
            f"- 成交金額: {taiex.get('volume_amount')}",
            f"- 資料日期: {taiex.get('date')}",
        ]
    else:
        lines.append("(大盤即時資料抓取中)")

    lines += [
        "",
        "# 個股技術分析彙整",
        f"- 共分析 {ms['total_analyzed']} 檔個股",
        "",
        "## 今日強勢股 (漲幅 Top 5)",
    ]
    for sym, pct in ms["top_gainers"]:
        a = ta.get(sym, {})
        lines.append(
            f"  - {sym}: +{pct:.2f}%  收盤={a.get('last_price','?')}  "
            f"RSI={a.get('rsi','?')}  量比={a.get('volume_ratio','?')}"
        )

    lines += ["", "## 今日弱勢股 (跌幅 Top 5)"]
    for sym, pct in ms["top_losers"]:
        a = ta.get(sym, {})
        lines.append(
            f"  - {sym}: {pct:.2f}%  收盤={a.get('last_price','?')}  "
            f"RSI={a.get('rsi','?')}  量比={a.get('volume_ratio','?')}"
        )

    lines += ["", "## 超買警訊 (RSI > 70)"]
    for sym, rsi in ms["overbought"]:
        lines.append(f"  - {sym}: RSI={rsi:.1f}")
    if not ms["overbought"]:
        lines.append("  (無)")

    lines += ["", "## 超賣訊號 (RSI < 30)"]
    for sym, rsi in ms["oversold"]:
        lines.append(f"  - {sym}: RSI={rsi:.1f}")
    if not ms["oversold"]:
        lines.append("  (無)")

    lines += ["", "## 爆量個股 (量比 > 1.5x)"]
    for sym, vr in ms["high_volume"]:
        lines.append(f"  - {sym}: {vr:.1f}x 均量")
    if not ms["high_volume"]:
        lines.append("  (無)")

    # Individual stock detail
    lines += ["", "# 重點個股詳細分析"]
    key_tickers = ["2330.TW", "2454.TW", "2317.TW", "2308.TW", "2882.TW", "^TWII"]
    for sym in key_tickers:
        a = ta.get(sym, {})
        if "error" in a:
            continue
        sector = SECTOR_MAP.get(sym, "指數" if "^" in sym else "其他")
        lines += [
            f"",
            f"## {sym} ({sector})",
            f"- 最新收盤: {a.get('last_price')} | 漲跌: {a.get('change_pct', 0):+.2f}%",
            f"- 趨勢: {a.get('trend')} | RSI: {a.get('rsi')} | 量比: {a.get('volume_ratio')}x",
            f"- MACD: {a.get('macd')} / Signal: {a.get('macd_signal')} / Hist: {a.get('macd_hist')}",
            f"- BB上軌: {a.get('bb_upper')} / 中軌: {a.get('bb_mid')} / 下軌: {a.get('bb_lower')}",
            f"- MA5: {a.get('sma5')} | MA20: {a.get('sma20')}",
            f"- 期間高/低: {a.get('period_high')} / {a.get('period_low')}",
            f"- 技術訊號: {', '.join(a.get('signals', [])) or '無特殊訊號'}",
        ]

    lines += [
        "",
        "# 分析要求",
        "請根據以上資料提供：",
        "1. **大盤走勢研判** — 目前市場氛圍（多/空/盤整）及短期展望",
        "2. **產業板塊分析** — 哪些產業強弱，資金輪動跡象",
        "3. **重點個股觀察** — 特別值得注意的機會或風險",
        "4. **技術面警示** — 超買/超賣/突破/爆量等關鍵訊號",
        "5. **下一小時策略建議** — 短線操作方向及關鍵支撐/壓力位",
        "",
        "格式要求：使用繁體中文，條列清晰，重點以**粗體**標示。",
        f"分析時間: {data['analysis_time']}",
    ]

    return "\n".join(lines)


def run_analysis() -> dict:
    """
    Full pipeline: collect data → build prompt → call Claude → return report.
    Returns a dict with 'report' (str) and 'raw_data' (dict).
    """
    print(f"\n{'='*60}")
    print(f"  台灣股市 AI 分析啟動  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # Step 1: collect data
    data = _collect_market_data()

    # Step 2: build prompt
    prompt = _build_prompt(data)

    # Step 3: call Claude with adaptive thinking + streaming
    print("  [AI] 呼叫 Claude 進行市場分析 (streaming)...")
    client = _get_client()

    report_chunks: list[str] = []

    with client.messages.stream(
        model=MODEL,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=(
            "你是一位專業的台灣股市技術分析師，擁有豐富的技術指標解讀經驗。"
            "請根據提供的量化數據，以精準、客觀的角度分析市場，"
            "並提供具體可操作的見解。所有分析以繁體中文呈現。"
        ),
        messages=[{"role": "user", "content": prompt}],
        cache_control={"type": "ephemeral"},
    ) as stream:
        for text in stream.text_stream:
            report_chunks.append(text)
            print(text, end="", flush=True)

        final = stream.get_final_message()

    print(f"\n\n  [完成] 輸入 tokens: {final.usage.input_tokens} | "
          f"輸出 tokens: {final.usage.output_tokens} | "
          f"快取讀取: {final.usage.cache_read_input_tokens}")

    report = "".join(report_chunks)
    return {
        "report": report,
        "raw_data": data,
        "model": MODEL,
        "usage": {
            "input_tokens": final.usage.input_tokens,
            "output_tokens": final.usage.output_tokens,
            "cache_read": final.usage.cache_read_input_tokens,
            "cache_created": final.usage.cache_creation_input_tokens,
        },
        "generated_at": datetime.now().isoformat(),
    }
