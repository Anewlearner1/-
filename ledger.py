"""
判斷帳本與結算 —— 讓投資團隊的勝率、賠率、期望值第一次變成可觀測的東西。

設計概念（四個名詞，其餘都是實作細節）：
    Call        一位分析師對一檔標的、帶截止日的可證偽預測。寫入後不可變。
    Resolution  Call 到期時的客觀結果，純粹由價格算出，不得有 LLM 參與 ——
                否則計分會和被計分的東西一樣不可靠。
    Ledger      Call 的儲存；結算結果回寫，但 Call 本身永不修改。
    Scorecard   從帳本推導的統計，永不儲存 —— 避免真相有兩份。

核心單位是 R 倍數而非勝率：
    R = (出場 - 進場) / (進場 - 停損)
一個數字同時承載勝率與賠率，而「平均 R」就是期望值本身。
只看勝率會獎勵賺小賠大的爛策略。

對外介面刻意只有四個動詞：
    Ledger.record(meeting)          把一場會議的判斷收進帳本
    Ledger.resolve_due(price_fn)    結算所有到期的判斷
    Ledger.pending() / .resolved()  查看未結 / 已結
    Ledger.scorecard()              拿到每位分析師的統計
"""
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_HORIZON_DAYS = 90          # 解析不出時間框架時的保守預設（約一季）
HIGH_CONVICTION = 7                # 校準分桶：>= 此值算「高信心」
_UNIT_DAYS = {"日": 1, "天": 1, "週": 7, "周": 7, "月": 30.44, "年": 365}

# 先抓「數字（可帶區間）+ 單位」整組，避免被句子後面的年份（例如 2027-28）騙走
_HORIZON_RE = re.compile(
    r"(\d+)\s*(?:[-~－—到至]\s*(\d+))?\s*(?:個)?\s*(日|天|週|周|月|年)"
)


def parse_horizon_days(text: str) -> int:
    """把「3-6 個月」這類自由文字換成天數；區間取中點，看不懂就回預設值。"""
    m = _HORIZON_RE.search(str(text or ""))
    if not m:
        return DEFAULT_HORIZON_DAYS
    lo, hi, unit = float(m.group(1)), m.group(2), m.group(3)
    n = (lo + float(hi)) / 2 if hi else lo
    return max(1, int(round(n * _UNIT_DAYS[unit])))


# --------------------------------------------------------------------------
# 結算：純函數，同樣的輸入永遠得到同樣的結果
# --------------------------------------------------------------------------

def resolve_call(call: dict, bars) -> dict:
    """
    用日 K 走一遍價格路徑，判定這個 Call 的結果。

    停損與目標同日觸及時一律判停損 —— 日 K 看不出盤中順序，
    保守假設，寧可低估自己。
    """
    stance = str(call.get("stance", "")).upper()
    if stance == "HOLD":
        return _resolution("ABSTAINED", None, None, None)
    if bars is None or len(bars) == 0:
        return _resolution("NO_DATA", None, None, None)

    entry = float(call["entry"])
    stop, target = call.get("stop"), call.get("target")
    is_long = stance == "BUY"

    exit_price, outcome, hit_date = None, "EXPIRED", None
    for ts, bar in bars.iterrows():
        hi, lo = float(bar["High"]), float(bar["Low"])
        stopped = stop is not None and (lo <= stop if is_long else hi >= stop)
        reached = target is not None and (hi >= target if is_long else lo <= target)
        if stopped:                      # 同日兩者皆觸及也走這條：保守
            exit_price, outcome, hit_date = float(stop), "STOP_HIT", ts
            break
        if reached:
            exit_price, outcome, hit_date = float(target), "TARGET_HIT", ts
            break
    if exit_price is None:
        exit_price = float(bars.iloc[-1]["Close"])
        hit_date = bars.index[-1]

    pct = (exit_price - entry) / entry * 100 * (1 if is_long else -1)
    r = None
    if stop is not None:
        risk = abs(entry - float(stop))
        if risk > 0:
            # 打到停損的定義就是 -1R，不受滑價假設影響
            r = -1.0 if outcome == "STOP_HIT" else \
                (exit_price - entry) / risk * (1 if is_long else -1)
    return _resolution(outcome, r, pct, exit_price, hit_date)


def _resolution(outcome, r, pct, exit_price, hit_date=None) -> dict:
    return {
        "outcome": outcome,
        "r_multiple": None if r is None else round(r, 3),
        "pct_return": None if pct is None else round(pct, 2),
        "exit_price": exit_price,
        "exit_date": str(getattr(hit_date, "date", lambda: hit_date)()) if hit_date is not None else None,
        "resolved_at": datetime.now().isoformat(timespec="seconds"),
    }


# --------------------------------------------------------------------------
# 帳本
# --------------------------------------------------------------------------

class Ledger:
    """Call 的持久化儲存。一行一個 JSON，結算結果回寫到同一筆。"""

    def __init__(self, path="reports/ledger.jsonl"):
        self.path = Path(path)

    # ---- 讀寫 ----
    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(ln) for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def _write(self, records: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8",
        )

    def _append(self, records: list[dict]) -> None:
        self._write(self._load() + records)

    # ---- 對外的四個動詞 ----
    def record(self, meeting: dict) -> int:
        """
        把一場會議的每位分析師判斷收進帳本，回傳新增筆數。

        以 (會議時間, 標的, 分析師) 產生確定性 id，因此重跑腳本不會重複寫入 ——
        重跑是常態，重複計入會直接污染統計。
        """
        existing = {r["call_id"] for r in self._load()}
        made = _as_date(meeting.get("meeting_time")) or date.today()
        packet = (meeting.get("market_packet") or {}).get("technicals", {})

        new = []
        for sym, c in (meeting.get("consensus") or {}).items():
            if "positions" not in c:
                continue
            entry = (packet.get(sym) or {}).get("last_price")
            for p in c["positions"]:
                cid = _call_id(meeting.get("meeting_time"), sym, p["analyst"])
                if cid in existing or entry is None:
                    continue
                days = parse_horizon_days(p.get("time_horizon"))
                new.append({
                    "call_id": cid,
                    "made_date": str(made),
                    "due_date": str(made + timedelta(days=days)),
                    "horizon_days": days,
                    "symbol": sym,
                    "analyst": p["analyst"],
                    "stance": p["stance"],
                    "conviction": p.get("conviction"),
                    "entry": float(entry),
                    "target": p.get("target_price"),
                    "stop": p.get("stop_loss"),
                    "resolution": None,
                })
        if new:
            self._append(new)
        return len(new)

    def resolve_due(self, price_fn, today: date | None = None) -> int:
        """
        結算所有已到期的判斷，回傳結算筆數。

        price_fn(symbol, start, end) 需回傳含 High/Low/Close 的日 K。
        沒到期的一律不碰 —— 提早看答案會讓統計偏向短線。
        """
        today = today or date.today()
        records, n = self._load(), 0
        for r in records:
            if r.get("resolution") or _as_date(r["due_date"]) > today:
                continue
            bars = price_fn(r["symbol"], _as_date(r["made_date"]), _as_date(r["due_date"]))
            r["resolution"] = resolve_call(r, bars)
            n += 1
        if n:
            self._write(records)
        return n

    def pending(self) -> list[dict]:
        return [r for r in self._load() if not r.get("resolution")]

    def resolved(self) -> list[dict]:
        return [r for r in self._load() if r.get("resolution")]

    def scorecard(self) -> dict:
        """
        每位分析師的統計，全部從帳本現算。

        棄權（HOLD）與無法結算的不進 R 統計，但單獨列出 ——
        棄權率過高本身就是訊號。
        """
        by: dict[str, list] = {}
        for r in self.resolved():
            by.setdefault(r["analyst"], []).append(r)

        out = {}
        for aid, rows in by.items():
            scored = [r for r in rows if r["resolution"].get("r_multiple") is not None]
            rs = [r["resolution"]["r_multiple"] for r in scored]
            wins, losses = [x for x in rs if x > 0], [x for x in rs if x <= 0]
            out[aid] = {
                "n": len(rs),
                "abstained": sum(1 for r in rows if r["resolution"]["outcome"] == "ABSTAINED"),
                "unresolvable": sum(1 for r in rows if r["resolution"]["outcome"] == "NO_DATA"),
                "win_rate": (len(wins) / len(rs)) if rs else None,
                "avg_r": (sum(rs) / len(rs)) if rs else None,      # 期望值本身
                "total_r": round(sum(rs), 2) if rs else None,
                "payoff_ratio": (
                    (sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
                    if wins and losses else None
                ),
                "calibration": _calibration(scored),
            }
        return out


def _calibration(scored: list[dict]) -> dict:
    """信心度要能被驗證：說 9/10 的時候是不是真的比說 5/10 準。"""
    out = {}
    for name, keep in (("high", lambda c: c >= HIGH_CONVICTION),
                       ("low", lambda c: c < HIGH_CONVICTION)):
        rows = [r for r in scored if keep(r.get("conviction") or 0)]
        rs = [r["resolution"]["r_multiple"] for r in rows]
        out[name] = {
            "n": len(rs),
            "win_rate": (sum(1 for x in rs if x > 0) / len(rs)) if rs else None,
            "avg_r": (sum(rs) / len(rs)) if rs else None,
        }
    return out


def _call_id(meeting_time, symbol, analyst) -> str:
    return hashlib.sha1(f"{meeting_time}|{symbol}|{analyst}".encode()).hexdigest()[:16]


def _as_date(value):
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "")).date()
