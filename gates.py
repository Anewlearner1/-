"""
分層觸發 —— token 要花在有爭議的地方，那也正是資訊最多的地方。

兩道純函數閘門，都不呼叫模型：

    packet_is_usable   資料包是空的就別開會。先前有整場會議五個人各寫一段
                       「資料不足，信心度下修」，那是一份看起來專業但零資訊
                       的報告，比不跑更糟。
    needs_cross_exam   R1 若全員同向且機率接近，R2 只會產出五份「維持原判」。
                       有分歧才辯論。

刻意不做的事：用規則預判「這檔沒機會」而跳過會議。規則篩不掉的正是非顯而
易見的機會，那是團隊存在的理由。閘門只擋「資料不足」與「無事可辯」，
不替分析師做判斷。
"""

# 立場一致時，機率離散到什麼程度仍算有爭議
P_DISPERSION_THRESHOLD = 0.15
# 低於這個人數的「共識」不算共識，樣本太小
MIN_VOICES_FOR_CONSENSUS = 4


def packet_is_usable(packet: dict) -> tuple[bool, str]:
    """資料包裡至少要有一檔標的算得出現價，否則開會只是浪費。"""
    tech = packet.get("technicals") or {}
    usable = [s for s in packet.get("symbols", [])
              if (tech.get(s) or {}).get("last_price") is not None]
    if usable:
        return True, ""
    return False, ("沒有任何標的取得可用的價格資料 —— 開會只會得到五份"
                   "「資料不足」，請先確認網路與資料來源")


def needs_cross_exam(round1: dict, symbols: list[str]) -> tuple[bool, str]:
    """
    判斷是否值得跑第二輪交叉質詢。

    只要有一檔標的有爭議就跑 —— 質詢是整場進行的，無法只針對單一標的。
    """
    voices = [r for r in round1.values() if "error" not in r]
    if not voices:
        return False, "第一輪無人成功發言，第二輪也救不了"

    if len(voices) < MIN_VOICES_FOR_CONSENSUS:
        return True, (f"僅 {len(voices)} 人成功發言，人數太少不足以構成共識，"
                      f"仍需交叉質詢")

    for sym in symbols:
        calls = [c for r in voices for c in r.get("calls", [])
                 if str(c.get("symbol", "")).strip().upper() == sym.strip().upper()]
        if len(calls) < 2:
            continue

        if len({c.get("stance") for c in calls}) > 1:
            return True, f"{sym} 立場分歧，需要交叉質詢"

        ps = [float(c["p_target"]) for c in calls if c.get("p_target") is not None]
        if len(ps) >= 2 and (max(ps) - min(ps)) > P_DISPERSION_THRESHOLD:
            return True, (f"{sym} 立場雖一致，但目標達成機率從 {min(ps):.0%} "
                          f"到 {max(ps):.0%}，其實沒有共識")

    return False, "全員立場一致且機率接近，第二輪只會產出「維持原判」，略過"
