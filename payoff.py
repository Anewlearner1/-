"""
賠率結構 —— 把「方向」變成「期望值」。

報酬來自勝率 × 賠率 × 部位，不是方向本身。40% 勝率配 3:1 賠率，
長期勝過 60% 勝率配 1:1。這個模組把一個判斷換算成可比較的期望值。

分工原則：LLM 負責估機率與價位（那需要判斷），算術由這裡做（那不該交給
會算錯的東西）。所有函數都是純函數，同樣輸入永遠同樣輸出。

    R_target   = (目標 - 進場) / (進場 - 停損)      賭贏能拿幾倍風險
    expected_r = p × R_target - (1 - p) × 1        期望值本身
    signed     = expected_r × 方向                  站在多方視角的貢獻

最後一項是關鍵：BUY 的 +2R 和 SELL 的 +2R 是相反立場，直接平均會得到
毫無意義的數字。轉成有號之後才能加總。
"""

MIN_R_TARGET = 2.0   # 賠率門檻：低於此值不算可執行，高勝率低賠率是常見的破產方式
MIN_EXPECTED_R = 0.0  # 期望值門檻


def r_target(stance, entry, stop, target):
    """賠率倍數；任何一項缺失或方向矛盾就回 None —— 假精確比沒有更危險。"""
    stance = str(stance or "").upper()
    if stance not in ("BUY", "SELL") or None in (entry, stop, target):
        return None
    entry, stop, target = float(entry), float(stop), float(target)
    risk = abs(entry - stop)
    if risk == 0:
        return None
    reward = (target - entry) if stance == "BUY" else (entry - target)
    if reward <= 0:          # BUY 卻把目標訂在進場之下（或反之）：自相矛盾
        return None
    return reward / risk


def expected_r(p, rt):
    """期望值。機率超出 0-1 時夾限 —— 模型偶爾會給 1.2。"""
    if p is None or rt is None:
        return None
    p = min(max(float(p), 0.0), 1.0)
    return p * float(rt) - (1.0 - p) * 1.0


def assess(call: dict) -> dict:
    """
    完整評估一個判斷，回傳賠率、期望值、是否可執行、以及不可執行的原因。

    signed_expected_r 是給團隊加總用的：站在「做多這檔股票」的視角，
    這位分析師的意見值多少期望值。算不出來時記 0 而非猜測。
    """
    stance = str(call.get("stance") or "").upper()
    rt = r_target(stance, call.get("entry"), call.get("stop"), call.get("target"))
    er = expected_r(call.get("p_target"), rt)

    if stance == "HOLD":
        reason = "觀望：不持有部位，對多方視角的期望值貢獻為零"
    elif rt is None:
        missing = "停損" if call.get("stop") is None else \
                  "目標價" if call.get("target") is None else "有效的目標／停損（方向矛盾或零風險）"
        reason = f"缺少{missing}，算不出賠率"
    elif er is None:
        reason = "缺少目標達成機率，算不出期望值"
    elif rt < MIN_R_TARGET:
        reason = f"賠率 {rt:.1f}:1 未達 {MIN_R_TARGET:.0f}:1 門檻"
    elif er <= MIN_EXPECTED_R:
        reason = f"期望值 {er:+.2f}R 不為正"
    else:
        reason = f"賠率 {rt:.1f}:1、期望值 {er:+.2f}R"

    actionable = bool(rt is not None and er is not None
                      and rt >= MIN_R_TARGET and er > MIN_EXPECTED_R)
    direction = {"BUY": 1.0, "SELL": -1.0}.get(stance, 0.0)
    return {
        "r_target": None if rt is None else round(rt, 2),
        "expected_r": None if er is None else round(er, 3),
        "signed_expected_r": 0.0 if er is None else round(er * direction, 3),
        "actionable": actionable,
        "reason": reason,
    }


def brier_score(pairs) -> float | None:
    """
    機率校準品質：mean((p - 實際)²)，越低越好。

    0.0 = 完美；0.25 = 等於每次都猜 50%；> 0.25 = 比擲硬幣還糟。
    這是 conviction 那種 0-10 感覺分數做不到的事 —— 機率可以被證偽。
    """
    pairs = list(pairs)
    if not pairs:
        return None
    return sum((min(max(float(p), 0.0), 1.0) - (1.0 if hit else 0.0)) ** 2
               for p, hit in pairs) / len(pairs)
