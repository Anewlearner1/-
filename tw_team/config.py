"""Configuration for the Taiwan stock investment team.

Everything here can be overridden with environment variables so the team can be
tuned without editing code. All money values are TWD (新台幣).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return list(default)
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


# --------------------------------------------------------------------------- #
# Goal
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GoalConfig:
    start_capital: float = _env_float("GOAL_START_CAPITAL", 1_000_000.0)
    target_capital: float = _env_float("GOAL_TARGET_CAPITAL", 3_000_000.0)
    horizon_years: float = _env_float("GOAL_HORIZON_YEARS", 3.0)
    # ISO date the clock started. Defaults to the first run (stored in portfolio state).
    start_date: str | None = os.environ.get("GOAL_START_DATE") or None


# --------------------------------------------------------------------------- #
# Universe
# --------------------------------------------------------------------------- #
# Market-regime instruments the macro strategist looks at.
MACRO_SYMBOLS: list[str] = [
    "^TWII",   # 加權指數 (TAIEX)
    "^TWOII",  # 櫃買指數 (TPEX index)
    "0050.TW", # 元大台灣50（大盤代理）
    "^VIX",    # 美股波動率（連動外資風險偏好）
    "^TNX",    # 美債10年殖利率（連動外資資金流向）
    "TWD=X",   # 美元/新台幣匯率
    "^SOX",    # 費城半導體指數（連動台股半導體權值）
]

SECTOR_ETFS: dict[str, str] = {
    "0050.TW": "台灣50（大盤代理）",
    "0056.TW": "高股息",
    "00878.TW": "高股息（月配）",
    "00929.TW": "科技高息（月配）",
}

# Board-lot ("整股") listed-market universe: weighted heavyweights + AI/半導體供應鏈.
LISTED_WATCHLIST: list[str] = [
    "2330.TW", "2317.TW", "2454.TW", "2382.TW", "3231.TW", "3661.TW",
    "2308.TW", "3037.TW", "6669.TW", "2412.TW", "2882.TW", "2881.TW",
    "2303.TW", "3008.TW", "2891.TW",
]

# OTC (上櫃, .TWO) small/mid-cap momentum names — higher volatility, lower liquidity.
OTC_WATCHLIST: list[str] = [
    "5274.TWO", "6488.TWO", "3529.TWO", "8069.TWO", "6415.TWO",
]

# Thematic / cyclical rotation names (shipping, biotech, traditional industry).
THEMATIC_WATCHLIST: list[str] = [
    "2603.TW", "2609.TW", "2618.TW", "6446.TW", "1477.TW",
]

DEFAULT_WATCHLIST: list[str] = [*LISTED_WATCHLIST, *OTC_WATCHLIST, *THEMATIC_WATCHLIST]

WATCHLIST: list[str] = _env_list("TW_WATCHLIST", DEFAULT_WATCHLIST)

# Symbols traded on TPEX (上櫃) rather than TWSE (上市) — used for OTC-specific
# risk limits (lower max position) and to route chip-data fetches to the right API.
OTC_SYMBOLS: set[str] = {s for s in DEFAULT_WATCHLIST if s.endswith(".TWO")} | {
    s.upper() for s in os.environ.get("TW_OTC_SYMBOLS", "").split(",") if s.strip()
}

SECTOR_MAP: dict[str, str] = {
    "2330.TW": "半導體", "2303.TW": "半導體", "2454.TW": "半導體", "2382.TW": "AI 伺服器/PC",
    "3231.TW": "AI 伺服器", "3661.TW": "IC 設計", "3037.TW": "PCB",
    "6669.TW": "AI 伺服器", "3008.TW": "光學",
    "2317.TW": "電子製造", "2308.TW": "電子零件",
    "2412.TW": "電信",
    "2882.TW": "金融", "2881.TW": "金融", "2891.TW": "金融",
    "5274.TWO": "軟體/IC設計", "6488.TWO": "半導體材料", "3529.TWO": "IC設計",
    "8069.TWO": "連接器", "6415.TWO": "IC設計",
    "2603.TW": "航運", "2609.TW": "航運", "2618.TW": "航空",
    "6446.TW": "生技/藥用玻璃", "1477.TW": "紡織",
}


# --------------------------------------------------------------------------- #
# Risk limits — hard limits enforced in code, regardless of what the LLM says
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RiskLimits:
    max_position_pct: float = _env_float("RISK_MAX_POSITION_PCT", 0.25)       # of equity, listed (上市)
    max_position_pct_otc: float = _env_float("RISK_MAX_POSITION_PCT_OTC", 0.15)  # of equity, OTC (上櫃)
    max_gross_exposure: float = _env_float("RISK_MAX_GROSS_EXPOSURE", 1.0)    # 1.0 = no margin
    max_positions: int = _env_int("RISK_MAX_POSITIONS", 6)
    min_cash_pct: float = _env_float("RISK_MIN_CASH_PCT", 0.05)
    default_stop_loss_pct: float = _env_float("RISK_DEFAULT_STOP_LOSS_PCT", 0.12)
    max_stop_loss_pct: float = _env_float("RISK_MAX_STOP_LOSS_PCT", 0.25)
    max_trades_per_run: int = _env_int("RISK_MAX_TRADES_PER_RUN", 6)
    # Below this drawdown from the equity peak the team may only reduce risk.
    drawdown_halt_pct: float = _env_float("RISK_DRAWDOWN_HALT_PCT", 0.25)
    # Minimum order size to avoid dust trades (below one board lot at most watchlist prices).
    min_order_twd: float = _env_float("RISK_MIN_ORDER_TWD", 20_000.0)
    # Taiwan daily price-limit band: ±10% from the previous close.
    price_limit_pct: float = _env_float("RISK_PRICE_LIMIT_PCT", 0.10)
    # Trading cost model (see tw_team/portfolio.py for how these are applied).
    buy_fee_rate: float = _env_float("TW_BUY_FEE_RATE", 0.001425)
    sell_fee_rate: float = _env_float("TW_SELL_FEE_RATE", 0.001425)
    sell_tax_rate: float = _env_float("TW_SELL_TAX_RATE", 0.003)
    min_fee_twd: float = _env_float("TW_MIN_FEE_TWD", 20.0)
    board_lot_shares: int = _env_int("TW_BOARD_LOT_SHARES", 1000)


# --------------------------------------------------------------------------- #
# Model / runtime
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ModelConfig:
    model: str = os.environ.get("TW_TEAM_MODEL", "claude-opus-5")
    analyst_effort: str = os.environ.get("TW_TEAM_ANALYST_EFFORT", "medium")
    pm_effort: str = os.environ.get("TW_TEAM_PM_EFFORT", "high")
    # Let the news/catalyst analyst use Anthropic's server-side web search.
    web_search: bool = os.environ.get("TW_TEAM_WEB_SEARCH", "1") == "1"
    web_search_max_uses: int = _env_int("TW_TEAM_WEB_SEARCH_MAX_USES", 6)
    stream: bool = os.environ.get("TW_TEAM_STREAM", "1") == "1"


@dataclass(frozen=True)
class RuntimeConfig:
    state_dir: Path = field(default_factory=lambda: Path(os.environ.get("TW_TEAM_STATE_DIR", "./state")))
    report_dir: Path = field(default_factory=lambda: Path(os.environ.get("TW_TEAM_REPORT_DIR", "./reports/tw")))
    history_period: str = os.environ.get("TW_TEAM_HISTORY_PERIOD", "6mo")
    history_interval: str = os.environ.get("TW_TEAM_HISTORY_INTERVAL", "1d")
    market_open_only: bool = os.environ.get("MARKET_OPEN_ONLY", "0") == "1"


GOAL = GoalConfig()
RISK = RiskLimits()
MODEL = ModelConfig()
RUNTIME = RuntimeConfig()
