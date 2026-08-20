"""
LLM 後端 — 讓投資團隊可以用「Claude 訂閱帳號」或「API key」兩種方式跑。

預設 claude_cli：透過 Claude Agent SDK 呼叫本機已登入的 Claude Code CLI，
用你的 Claude 訂閱額度，不需要 ANTHROPIC_API_KEY，也不會按 token 另外計費。

用 TEAM_BACKEND 環境變數切換：
    TEAM_BACKEND=claude_cli   # 預設，走訂閱登入
    TEAM_BACKEND=api          # 走 ANTHROPIC_API_KEY

兩個後端都吃同一份 json_schema，回傳同樣的 (結構化結果, 用量) 形狀，
所以 discussion.py 不需要知道底下是哪一種。
"""
import asyncio
import json
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor

BACKEND = os.environ.get("TEAM_BACKEND", "claude_cli").lower()
EFFORT = os.environ.get("TEAM_EFFORT", "high")  # low | medium | high | xhigh | max

# claude_cli 用別名（由已登入的 CLI 解析成實際模型）；api 用完整模型 ID
CLI_MODEL = os.environ.get("TEAM_MODEL", "opus")
API_MODEL = os.environ.get("TEAM_MODEL", "claude-opus-5")
MAX_TOKENS = 16000


class BackendError(RuntimeError):
    """後端呼叫失敗，訊息已整理成人看得懂的形式。"""


def describe() -> str:
    if BACKEND == "api":
        return f"api（ANTHROPIC_API_KEY，model={API_MODEL}, effort={EFFORT}）"
    return f"claude_cli（Claude 訂閱登入，免 API key，model={CLI_MODEL}, effort={EFFORT}）"


# --------------------------------------------------------------------------
# 共用工具
# --------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """從回覆文字中挖出 JSON 物件（容忍 ``` 圍欄與前後贅字）。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 退而求其次：掃出第一個完整的大括號區塊
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(cleaned):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                return json.loads(cleaned[start:i + 1])
    raise BackendError("回覆中找不到合法的 JSON，模型可能沒有照結構輸出")


def _empty_usage() -> dict:
    return {"input_tokens": 0, "output_tokens": 0, "cache_read": 0,
            "cache_created": 0, "cost_usd": 0.0}


# --------------------------------------------------------------------------
# 後端一：claude_cli（Claude 訂閱登入，免 API key）
# --------------------------------------------------------------------------

def _preflight_cli() -> None:
    if shutil.which("claude") is None:
        raise BackendError(
            "找不到 claude 指令。請先安裝並登入 Claude Code：\n"
            "    npm install -g @anthropic-ai/claude-code\n"
            "    claude login\n"
            "（或改用 API key 模式：TEAM_BACKEND=api）"
        )
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError as e:
        raise BackendError(
            "缺少 claude-agent-sdk 套件，請執行：pip install -r requirements.txt"
        ) from e


def _flatten(messages: list[dict]) -> str:
    """把 messages 陣列攤平成單一 prompt（CLI 後端一次只吃一段文字）。"""
    parts = []
    for m in messages:
        if m["role"] == "assistant":
            parts.append("# 你在上一輪的回答（原文，供你自己參考）\n" + m["content"])
        else:
            parts.append(m["content"])
    return "\n\n".join(parts)


async def _ask_cli(req: dict) -> tuple[dict, dict]:
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query,
    )

    options = ClaudeAgentOptions(
        system_prompt=req["system"],
        model=CLI_MODEL,
        effort=EFFORT,
        thinking={"type": "adaptive"},
        output_format={"type": "json_schema", "schema": req["schema"]},
        allowed_tools=[],          # 純文字推理，不給任何工具
        max_turns=6,               # 沒有工具迴圈，留點餘裕給結構化輸出收束
        permission_mode="dontAsk",
        setting_sources=[],        # 不載入使用者 / 專案設定與 hooks，避免干擾
    )

    texts: list[str] = []
    result = None
    async for message in query(prompt=_flatten(req["messages"]), options=options):
        if isinstance(message, AssistantMessage):
            if message.error:
                raise BackendError(f"Claude CLI 回報錯誤：{message.error}")
            texts += [b.text for b in message.content if isinstance(b, TextBlock)]
        elif isinstance(message, ResultMessage):
            result = message

    if result is None:
        raise BackendError("Claude CLI 沒有回傳結果訊息（可能是登入過期，試試 claude login）")
    if result.is_error:
        detail = "；".join(result.errors or []) or result.subtype
        raise BackendError(f"Claude CLI 執行失敗：{detail}")

    data = result.structured_output
    if data is None:
        data = _extract_json(result.result or "".join(texts))

    raw = result.usage or {}
    return data, {
        "input_tokens": raw.get("input_tokens", 0),
        "output_tokens": raw.get("output_tokens", 0),
        "cache_read": raw.get("cache_read_input_tokens", 0),
        "cache_created": raw.get("cache_creation_input_tokens", 0),
        "cost_usd": result.total_cost_usd or 0.0,
    }


async def _ask_many_cli(requests: list[dict], on_done) -> dict:
    async def one(req):
        try:
            data, usage = await _ask_cli(req)
            on_done(req, None)
            return req["key"], (data, usage, None)
        except Exception as e:  # noqa: BLE001 — 單一分析師失敗不該拖垮整場會議
            on_done(req, e)
            return req["key"], (None, _empty_usage(), e)

    return dict(await asyncio.gather(*(one(r) for r in requests)))


# --------------------------------------------------------------------------
# 後端二：api（ANTHROPIC_API_KEY）
# --------------------------------------------------------------------------

_api_client = None


def _preflight_api() -> None:
    try:
        import anthropic  # noqa: F401
    except ImportError as e:
        raise BackendError("缺少 anthropic 套件，請執行：pip install -r requirements.txt") from e
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise BackendError(
            "TEAM_BACKEND=api 需要 ANTHROPIC_API_KEY。\n"
            "若不想用 API key，改用訂閱登入模式：TEAM_BACKEND=claude_cli（預設）"
        )


def _ask_api(req: dict) -> tuple[dict, dict]:
    global _api_client
    import anthropic

    if _api_client is None:
        _api_client = anthropic.Anthropic()

    response = _api_client.messages.create(
        model=API_MODEL,
        max_tokens=MAX_TOKENS,
        system=req["system"],
        messages=req["messages"],
        thinking={"type": "adaptive"},
        output_config={
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": req["schema"]},
        },
        cache_control={"type": "ephemeral"},
    )
    text = next(b.text for b in response.content if b.type == "text")
    u = response.usage
    return json.loads(text), {
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cache_read": u.cache_read_input_tokens,
        "cache_created": u.cache_creation_input_tokens,
        "cost_usd": 0.0,  # API 模式不回報金額，成本看 token 用量
    }


def _ask_many_api(requests: list[dict], on_done) -> dict:
    def one(req):
        try:
            data, usage = _ask_api(req)
            on_done(req, None)
            return req["key"], (data, usage, None)
        except Exception as e:  # noqa: BLE001
            on_done(req, e)
            return req["key"], (None, _empty_usage(), e)

    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        return dict(pool.map(one, requests))


# --------------------------------------------------------------------------
# 對外介面
# --------------------------------------------------------------------------

def preflight() -> None:
    """開會前先檢查後端能不能用，早點噴出人看得懂的錯誤。"""
    if BACKEND == "api":
        _preflight_api()
    elif BACKEND == "claude_cli":
        _preflight_cli()
    else:
        raise BackendError(f"未知的 TEAM_BACKEND：{BACKEND}（可用：claude_cli、api）")


def ask_many(requests: list[dict]) -> dict:
    """
    平行送出多個結構化請求。

    每個 request 需含 key / label / system / messages / schema。
    回傳 {key: (結果 dict 或 None, 用量 dict, 例外或 None)}，
    單一分析師失敗不會中斷整場會議。
    """
    def on_done(req, error):
        mark = "✗" if error else "✓"
        tail = f" — {error}" if error else ""
        sys.stdout.write(f"       {mark} {req['label']}{tail}\n")
        sys.stdout.flush()

    if BACKEND == "api":
        return _ask_many_api(requests, on_done)
    return asyncio.run(_ask_many_cli(requests, on_done))
