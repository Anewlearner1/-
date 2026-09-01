"""
視角過濾的規格 —— 讓分歧來自證據差異，而非人格差異。

五個人讀同一份資料時，分歧只是同一個訊號的五種說法（有效獨立樣本數 ≈ 1）。
給不同的資料切片，分歧才承載資訊。順帶讓每份 prompt 變小。

誠信要求：每位分析師必須被告知自己沒看到什麼，否則會把「我沒被給」
誤讀成「不存在」。
"""
import lens
import pytest

PACKET = {
    "symbols": ["3037.TW"],
    "taiex": {"taiex": 24150.3, "change": -180.2, "volume_amount": "3800億", "date": "2026-08-30"},
    "market_breadth": {"up": 380, "down": 520, "unchanged": 90},
    "technicals": {"3037.TW": {
        "last_price": 1110.0, "change_pct": -5.93, "trend": "上升趨勢",
        "rsi": 64.1, "macd": 12.3, "macd_signal": 9.8, "macd_hist": 2.5,
        "bb_upper": 1210.0, "bb_mid": 1120.0, "bb_lower": 1030.0,
        "sma5": 1124.0, "sma20": 1046.5, "volume_ratio": 1.6,
        "period_high": 1230.0, "period_low": 985.0,
        "dist_from_high_pct": -9.8, "dist_from_low_pct": 12.7,
        "signals": ["接近布林上軌"]}},
    "fundamentals": {"3037.TW": {
        "name": "欣興", "sector": "電子零件", "market_cap": 1.7e12,
        "pe_ratio": 73.5, "52w_high": 1230.0, "52w_low": 260.0,
        "avg_volume": 27000000}},
    "period": "1mo", "interval": "1d", "collected_at": "2026-08-31T00:00:00",
}


def text_for(aid):
    return lens.for_analyst(PACKET, aid)


# --------------------------------------------------------------------------
# 每個學派看到自己用得上的，看不到用不上的
# --------------------------------------------------------------------------

def test_value_investor_gets_fundamentals_not_oscillators():
    t = text_for("buffett")
    assert "73.5" in t, "本益比是價值派的核心指標"
    assert "RSI" not in t and "MACD" not in t, "價值派不該被技術指標干擾判斷"


def test_quant_gets_price_action_not_fundamentals():
    t = text_for("simons")
    assert "RSI" in t and "MACD" in t
    assert "本益比" not in t, "量化派明言不採信循環高點的本益比"


def test_macro_gets_market_breadth():
    t = text_for("soros")
    assert "上漲家數" in t or "市場寬度" in t
    assert "24,150" in t or "24150" in t, "宏觀派需要大盤位置"


def test_tail_risk_gets_range_extremes_not_trend_signals():
    t = text_for("taleb")
    assert "985" in t, "區間低點是下檔測算的基礎"
    assert "MACD" not in t, "尾端風險派不看趨勢動能訊號"


def test_growth_investor_gets_sector_and_valuation():
    t = text_for("lynch")
    assert "電子零件" in t
    assert "73.5" in t


# --------------------------------------------------------------------------
# 誠信：必須揭露被拿掉了什麼
# --------------------------------------------------------------------------

@pytest.mark.parametrize("aid", ["buffett", "lynch", "soros", "simons", "taleb"])
def test_every_lens_discloses_what_was_withheld(aid):
    """看不到不等於不存在 —— 沒揭露會讓分析師把缺漏誤讀成訊號。"""
    t = text_for(aid)
    assert "已依你的學派過濾" in t
    assert "data_gaps" in t, "要告訴他缺的東西該寫進哪裡"


@pytest.mark.parametrize("aid", ["buffett", "lynch", "soros", "simons", "taleb"])
def test_every_lens_keeps_the_price_everyone_needs(aid):
    """進場價是所有人算賠率的共同基礎，任何視角都不能拿掉。"""
    assert "1,110" in text_for(aid) or "1110" in text_for(aid)


# --------------------------------------------------------------------------
# 省 token：每份切片都該明顯小於完整資料包
# --------------------------------------------------------------------------

@pytest.mark.parametrize("aid", ["buffett", "lynch", "soros", "simons", "taleb"])
def test_lens_is_smaller_than_full_packet(aid):
    from discussion import format_packet
    assert len(text_for(aid)) < len(format_packet(PACKET))


# --------------------------------------------------------------------------
# 故障時寧可給多不給少
# --------------------------------------------------------------------------

def test_unknown_analyst_gets_full_packet():
    """沒有定義視角的分析師拿完整資料 —— 少給資料會靜默降低品質，寧可多給。"""
    from discussion import format_packet
    assert lens.for_analyst(PACKET, "unknown") == format_packet(PACKET)


def test_missing_technicals_does_not_crash():
    broken = {**PACKET, "technicals": {"3037.TW": {"error": "資料不足"}}}
    t = lens.for_analyst(broken, "simons")
    assert "資料不足" in t


def test_coverage_reports_what_each_lens_sees():
    """要能對主理人交代每個人是根據什麼在發言。"""
    cov = lens.coverage()
    assert set(cov) == {"buffett", "lynch", "soros", "simons", "taleb"}
    assert "基本面" in cov["buffett"] and "技術面" in cov["simons"]


# --------------------------------------------------------------------------
# 市場背景必須與標的所屬市場相符
# --------------------------------------------------------------------------

US_PACKET = {
    "symbols": ["APH"],
    "taiex": {"taiex": 24150.3, "change": -180.2},
    "market_breadth": {"up": 380, "down": 520, "unchanged": 90},
    "technicals": {"APH": {"last_price": 158.55, "change_pct": 0.8,
                           "period_high": 165.0, "period_low": 140.0}},
    "fundamentals": {"APH": {"name": "Amphenol", "pe_ratio": 39.5,
                             "sector": "Technology"}},
    "period": "1mo", "interval": "1d", "collected_at": "t",
}


def test_us_stock_does_not_get_taiwan_index_as_market_context():
    """拿台股寬度推論美股會得到看似有據、實則無關的結論。"""
    t = lens.for_analyst(US_PACKET, "soros")
    assert "加權指數" not in t
    assert "24,150" not in t


def test_us_stock_discloses_missing_market_context():
    """沒有大盤資料要明說，並指引去查 —— 靜默省略會讓他以為不需要。"""
    t = lens.for_analyst(US_PACKET, "soros")
    assert "非台股" in t or "美股" in t
    assert "搜尋" in t


def test_taiwan_stock_still_gets_taiwan_market_context():
    assert "加權指數" in lens.for_analyst(PACKET, "soros")


@pytest.mark.parametrize("sym,is_tw", [
    ("2330.TW", True), ("8027.TWO", True), ("^TWII", True),
    ("APH", False), ("BRK-B", False), ("7203.T", False),
])
def test_market_detection(sym, is_tw):
    assert lens.is_taiwan_symbol(sym) is is_tw
