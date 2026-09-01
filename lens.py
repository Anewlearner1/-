"""
視角過濾 —— 讓分歧來自證據差異，而非人格差異。

五個人讀同一份資料時，他們的分歧只是同一個訊號的五種說法。西蒙斯自己在
3037 那場會議裡就點破了：「五位分析師端出的所有多頭證據不是五個獨立訊號，
是同一個因子的五種說法，相關係數趨近 1，有效獨立樣本數約為 1。」

平均相關的意見不會降低變異，只會製造虛假信心。給每個學派不同的資料切片，
分歧才承載資訊 —— 順帶讓每份 prompt 變小。

誠信要求：每位分析師都會被明確告知自己沒看到什麼。看不到不等於不存在，
沒揭露會讓他把缺漏誤讀成訊號。

對外只有兩個函數：
    for_analyst(packet, analyst_id)  取得該學派的資料切片文字
    coverage()                       每個視角看得到什麼（給主理人交代用）
"""
from fetcher import SECTOR_MAP


def _fmt(value, suffix: str = "") -> str:
    """呈現數值給分析師看；缺值要說「無資料」而不是留白。"""
    if value is None:
        return "無資料"
    if isinstance(value, float):
        return f"{value:,.2f}{suffix}"
    return f"{value}{suffix}"

# 每個學派看得到哪些區塊。未列出的學派拿完整資料包 —— 少給資料會靜默降低
# 品質，故障時寧可給多不給少。
_LENSES = {
    "buffett": {
        "sections": ("price", "fundamentals"),
        "sees": "基本面（本益比、市值、52週區間）與現價",
        "withheld": "技術指標、動能訊號、大盤寬度",
    },
    "lynch": {
        "sections": ("price", "fundamentals", "sector", "position_in_range"),
        "sees": "基本面、產業別、股價在區間中的位置",
        "withheld": "振盪指標（RSI／MACD／布林）、大盤寬度",
    },
    "soros": {
        "sections": ("price", "market", "position_in_range"),
        "sees": "大盤位置、市場寬度、個股相對區間位置",
        "withheld": "個股基本面細節、振盪指標",
    },
    "simons": {
        "sections": ("price", "technicals"),
        "sees": "技術面價量序列（RSI／MACD／布林／均線／量比）",
        "withheld": "基本面、產業敘事、大盤寬度",
    },
    "taleb": {
        "sections": ("price", "extremes"),
        "sees": "區間極值、波動幅度、距高低點距離",
        "withheld": "趨勢動能訊號、基本面、大盤寬度",
    },
}


def is_taiwan_symbol(symbol: str) -> bool:
    """台股（含上櫃與加權指數）才適用台股大盤資料。"""
    s = str(symbol or "").strip().upper()
    return s.endswith((".TW", ".TWO")) or s == "^TWII"


def coverage() -> dict:
    """每個視角看得到什麼 —— 讓主理人知道每個人是根據什麼在發言。"""
    return {aid: v["sees"] for aid, v in _LENSES.items()}


def for_analyst(packet: dict, analyst_id: str) -> str:
    """把資料包裁成該學派用得上的切片；未定義的學派拿完整資料包。"""
    spec = _LENSES.get(analyst_id)
    if spec is None:
        from discussion import format_packet  # 延遲匯入：只有退路需要，避免循環
        return format_packet(packet)

    sections = spec["sections"]
    lines = [
        "# 市場資料包（已依你的學派過濾）",
        f"資料時間：{packet.get('collected_at')}",
        f"你看得到：{spec['sees']}",
        f"**未提供**：{spec['withheld']}",
        "這些資料存在，只是不在你的視角內。若你的判斷確實需要它們，"
        "寫進 data_gaps 並據此下修信心 —— 不要把「沒看到」當成「不存在」。",
    ]

    if "market" in sections:
        # 台股大盤資料只對台股有意義。拿台股寬度推論美股會得到看似有據、
        # 實則無關的結論 —— 寧可明說沒有，也不要餵錯的背景。
        if all(is_taiwan_symbol(s) for s in packet.get("symbols", [])):
            t, b = packet.get("taiex", {}), packet.get("market_breadth", {})
            lines += [
                "", "## 大盤",
                f"- 加權指數：{_fmt(t.get('taiex'))}｜漲跌：{_fmt(t.get('change'))}"
                f"｜成交金額：{_fmt(t.get('volume_amount'))}",
                f"- 市場寬度：上漲 {_fmt(b.get('up'))}／下跌 {_fmt(b.get('down'))}"
                f"／平盤 {_fmt(b.get('unchanged'))}",
            ]
        else:
            lines += [
                "", "## 大盤",
                "本次標的非台股。本系統的大盤資料只涵蓋台灣市場，對此標的沒有"
                "參考價值，因此不提供 —— 餵錯的市場背景會得出看似有據、實則無關"
                "的結論。你需要的市場背景（所屬市場指數位置、利率、資金流向）請"
                "自行**搜尋**並附來源；查不到就據實下修信心並寫進 data_gaps。",
            ]

    for sym in packet.get("symbols", []):
        tech = (packet.get("technicals") or {}).get(sym, {})
        fund = (packet.get("fundamentals") or {}).get(sym, {})
        sector = SECTOR_MAP.get(sym, fund.get("sector", "未知"))
        lines += ["", f"## {sym} — {fund.get('name', sym)}"]

        if tech.get("error"):
            lines.append(f"- 技術面：{tech['error']}（請據此下修信心度）")
        else:
            # 進場價是所有人算賠率的共同基礎，任何視角都不能拿掉
            lines.append(f"- 現價：{_fmt(tech.get('last_price'))}"
                         f"｜當日漲跌：{_fmt(tech.get('change_pct'), '%')}")

        if "sector" in sections:
            lines.append(f"- 產業：{sector}")

        if "technicals" in sections and not tech.get("error"):
            lines += [
                f"- 趨勢：{_fmt(tech.get('trend'))}｜RSI：{_fmt(tech.get('rsi'))}"
                f"｜量比：{_fmt(tech.get('volume_ratio'), 'x')}",
                f"- MACD：{_fmt(tech.get('macd'))}／訊號 {_fmt(tech.get('macd_signal'))}"
                f"／柱狀 {_fmt(tech.get('macd_hist'))}",
                f"- 布林：上 {_fmt(tech.get('bb_upper'))}／中 {_fmt(tech.get('bb_mid'))}"
                f"／下 {_fmt(tech.get('bb_lower'))}",
                f"- 均線：MA5 {_fmt(tech.get('sma5'))}｜MA20 {_fmt(tech.get('sma20'))}",
                f"- 技術訊號：{'、'.join(tech.get('signals', [])) or '無'}",
            ]

        if "extremes" in sections and not tech.get("error"):
            lines += [
                f"- 期間高／低：{_fmt(tech.get('period_high'))}／{_fmt(tech.get('period_low'))}",
                f"- 距高點 {_fmt(tech.get('dist_from_high_pct'), '%')}"
                f"、距低點 {_fmt(tech.get('dist_from_low_pct'), '%')}",
                f"- 布林帶寬（波動幅度參考）：上 {_fmt(tech.get('bb_upper'))}"
                f"／下 {_fmt(tech.get('bb_lower'))}",
            ]

        if "position_in_range" in sections and not tech.get("error"):
            lines.append(f"- 期間高／低：{_fmt(tech.get('period_high'))}"
                         f"／{_fmt(tech.get('period_low'))}")

        if "fundamentals" in sections:
            if fund.get("error"):
                lines.append(f"- 基本面：抓取失敗（{fund['error']}）")
            else:
                lines += [
                    f"- 市值：{_fmt(fund.get('market_cap'))}｜本益比：{_fmt(fund.get('pe_ratio'))}",
                    f"- 52 週高／低：{_fmt(fund.get('52w_high'))}／{_fmt(fund.get('52w_low'))}",
                    f"- 均量：{_fmt(fund.get('avg_volume'))}",
                ]

    lines += [
        "",
        "## 資料限制",
        "本切片只含上列欄位，沒有財報全文、法說會、籌碼細節。"
        "需要而未取得的，寫進 data_gaps，不要自行推測數字。",
    ]
    return "\n".join(lines)
