"""Discord Webhook notifier for Taiwan stock market analysis reports."""
import os
import time
import requests

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# Discord embed colour constants
COLOR_GREEN  = 0x2ECC71
COLOR_RED    = 0xE74C3C
COLOR_YELLOW = 0xF1C40F
COLOR_BLUE   = 0x3498DB


def _split_message(text: str, limit: int = 1900) -> list[str]:
    """Split long text into Discord-safe chunks (≤ 2000 chars each)."""
    chunks, current = [], []
    for line in text.splitlines(keepends=True):
        if sum(len(c) for c in current) + len(line) > limit:
            chunks.append("".join(current))
            current = []
        current.append(line)
    if current:
        chunks.append("".join(current))
    return chunks or ["(無內容)"]


def _post(payload: dict, retries: int = 3) -> bool:
    """POST a single payload to Discord with retry logic."""
    if not DISCORD_WEBHOOK_URL:
        print("  [Discord] 未設定 DISCORD_WEBHOOK_URL，跳過推送")
        return False

    for attempt in range(1, retries + 1):
        try:
            r = requests.post(
                DISCORD_WEBHOOK_URL,
                json=payload,
                timeout=10,
                headers={"Content-Type": "application/json"},
            )
            if r.status_code == 429:
                retry_after = r.json().get("retry_after", 2)
                print(f"  [Discord] 速率限制，等待 {retry_after:.1f}s...")
                time.sleep(float(retry_after) + 0.5)
                continue
            r.raise_for_status()
            return True
        except Exception as e:
            wait = 2 ** attempt
            print(f"  [Discord] 推送失敗 (嘗試 {attempt}/{retries}): {e} — {wait}s 後重試")
            if attempt < retries:
                time.sleep(wait)
    return False


def send_report(result: dict) -> bool:
    """
    Send the analysis report to Discord.

    Sends:
      1. A rich embed header with key market metrics
      2. The full report text split into safe chunks
    """
    if not DISCORD_WEBHOOK_URL:
        print("  [Discord] 未設定 DISCORD_WEBHOOK_URL，跳過推送")
        return False

    report: str = result.get("report", "")
    generated_at: str = result.get("generated_at", "")
    usage: dict = result.get("usage", {})
    raw = result.get("raw_data", {})
    ms = raw.get("market_summary", {})
    taiex = raw.get("taiex", {})

    # Determine market sentiment colour from gainers vs losers
    gainers = len(ms.get("top_gainers", []))
    losers  = len(ms.get("top_losers",  []))
    color = COLOR_GREEN if gainers >= losers else COLOR_RED

    # Build key metrics for the embed
    fields = []

    if taiex.get("taiex"):
        change_str = taiex.get("change", "?")
        fields.append({
            "name": "📈 加權指數",
            "value": f"`{taiex['taiex']}` 漲跌 `{change_str}`",
            "inline": True,
        })

    if ms.get("top_gainers"):
        top = ms["top_gainers"][0]
        fields.append({
            "name": "🚀 最強個股",
            "value": f"`{top[0]}` +{top[1]:.2f}%",
            "inline": True,
        })

    if ms.get("top_losers"):
        worst = ms["top_losers"][0]
        fields.append({
            "name": "📉 最弱個股",
            "value": f"`{worst[0]}` {worst[1]:.2f}%",
            "inline": True,
        })

    if ms.get("overbought"):
        syms = ", ".join(f"`{s}`" for s, _ in ms["overbought"][:3])
        fields.append({"name": "⚠️ RSI 超買", "value": syms, "inline": True})

    if ms.get("oversold"):
        syms = ", ".join(f"`{s}`" for s, _ in ms["oversold"][:3])
        fields.append({"name": "🔻 RSI 超賣", "value": syms, "inline": True})

    if ms.get("high_volume"):
        syms = ", ".join(f"`{s}` {vr:.1f}x" for s, vr in ms["high_volume"][:3])
        fields.append({"name": "💥 爆量個股", "value": syms, "inline": True})

    # Header embed
    header_embed = {
        "title": "🇹🇼 台灣股市 AI 分析報告",
        "description": f"**分析時間:** {generated_at}\n**模型:** {result.get('model', 'claude-opus-4-7')}",
        "color": color,
        "fields": fields,
        "footer": {
            "text": (
                f"tokens: 輸入 {usage.get('input_tokens', 0):,} | "
                f"輸出 {usage.get('output_tokens', 0):,} | "
                f"快取命中 {usage.get('cache_read', 0):,}"
            )
        },
    }

    success = _post({"embeds": [header_embed]})
    if not success:
        return False

    # Send the report body in chunks
    chunks = _split_message(report)
    total = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        prefix = f"**📄 分析報告 ({i}/{total})**\n" if total > 1 else "**📄 分析報告**\n"
        payload = {"content": f"{prefix}```\n{chunk}\n```"}
        ok = _post(payload)
        if not ok:
            print(f"  [Discord] 第 {i}/{total} 段推送失敗")
        # Small delay to avoid hitting rate limits
        if i < total:
            time.sleep(0.8)

    print(f"  [Discord] 成功推送報告 ({total} 段訊息)")
    return True


def send_error(message: str) -> bool:
    """Send an error notification to Discord."""
    if not DISCORD_WEBHOOK_URL:
        return False

    embed = {
        "title": "❌ 台灣股市 Agent 執行錯誤",
        "description": f"```\n{message[:1800]}\n```",
        "color": COLOR_RED,
        "footer": {"text": "請檢查 Agent 執行狀況"},
    }
    return _post({"embeds": [embed]})


def send_startup(interval_minutes: int) -> bool:
    """Send a startup notification."""
    if not DISCORD_WEBHOOK_URL:
        return False

    embed = {
        "title": "🚀 台灣股市 AI Agent 已啟動",
        "description": (
            f"Agent 已成功啟動，將每 **{interval_minutes} 分鐘**執行一次分析。\n"
            "分析範圍：TAIEX 大盤 + 15 檔重點個股"
        ),
        "color": COLOR_BLUE,
        "fields": [
            {"name": "追蹤標的", "value": "台積電・聯發科・鴻海・大立光・台達電 等", "inline": False},
            {"name": "技術指標", "value": "RSI・MACD・布林通道・均線・成交量", "inline": False},
        ],
    }
    return _post({"embeds": [embed]})
