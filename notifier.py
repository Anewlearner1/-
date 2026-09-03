"""Generic Discord Webhook delivery primitives, shared by tw_team/report.py."""
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
