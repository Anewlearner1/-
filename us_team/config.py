"""Configuration for the US stock investment team.

Everything here can be overridden with environment variables so the team can be
tuned without editing code. All money values are USD.
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
    start_capital: float = _env_float("GOAL_START_CAPITAL", 30_000.0)
    target_capital: float = _env_float("GOAL_TARGET_CAPITAL", 1_000_000.0)
    horizon_years: float = _env_float("GOAL_HORIZON_YEARS", 3.0)
    # ISO date the clock started. Defaults to the first run (stored in portfolio state).
    start_date: str | None = os.environ.get("GOAL_START_DATE") or None


# --------------------------------------------------------------------------- #
# Universe
# --------------------------------------------------------------------------- #
# Market-regime instruments the macro strategist looks at.
MACRO_SYMBOLS: list[str] = [
    "SPY",   # S&P 500
    "QQQ",   # Nasdaq 100
    "IWM",   # Russell 2000
    "^VIX",  # Volatility index
    "TLT",   # 20y+ Treasuries
    "GLD",   # Gold
    "UUP",   # US Dollar index ETF
    "^TNX",  # 10y yield
]

SECTOR_ETFS: dict[str, str] = {
    "XLK": "科技",
    "SMH": "半導體",
    "XLF": "金融",
    "XLE": "能源",
    "XLV": "醫療",
    "XLY": "非必需消費",
    "XLI": "工業",
    "XLC": "通訊服務",
    "XLU": "公用事業",
    "ARKK": "高成長/創新",
}

# Tradeable universe. Skewed toward high-beta growth names because the goal
# requires far-above-market returns; the risk engine keeps sizing sane.
DEFAULT_WATCHLIST: list[str] = [
    "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AVGO",
    "AMD", "TSM", "ARM", "PLTR", "CRWD", "NFLX", "LLY", "COIN",
    "MSTR", "HOOD", "APP", "SMCI", "SOFI", "SHOP",
]

WATCHLIST: list[str] = _env_list("US_WATCHLIST", DEFAULT_WATCHLIST)

SECTOR_MAP: dict[str, str] = {
    "NVDA": "半導體", "AMD": "半導體", "TSM": "半導體", "ARM": "半導體", "AVGO": "半導體",
    "SMCI": "AI 伺服器",
    "AAPL": "消費電子", "MSFT": "軟體/雲端", "GOOGL": "網路/廣告", "META": "網路/廣告",
    "AMZN": "電商/雲端", "NFLX": "串流",
    "TSLA": "電動車/AI", "PLTR": "AI 軟體", "CRWD": "資安", "APP": "廣告科技", "SHOP": "電商軟體",
    "LLY": "製藥",
    "COIN": "加密貨幣", "MSTR": "比特幣代理", "HOOD": "券商/金融科技", "SOFI": "金融科技",
}


# --------------------------------------------------------------------------- #
# Risk limits — hard limits enforced in code, regardless of what the LLM says
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RiskLimits:
    max_position_pct: float = _env_float("RISK_MAX_POSITION_PCT", 0.25)   # of equity
    max_gross_exposure: float = _env_float("RISK_MAX_GROSS_EXPOSURE", 1.0)  # 1.0 = no leverage
    max_positions: int = _env_int("RISK_MAX_POSITIONS", 6)
    min_cash_pct: float = _env_float("RISK_MIN_CASH_PCT", 0.05)
    default_stop_loss_pct: float = _env_float("RISK_DEFAULT_STOP_LOSS_PCT", 0.12)
    max_stop_loss_pct: float = _env_float("RISK_MAX_STOP_LOSS_PCT", 0.25)
    max_trades_per_run: int = _env_int("RISK_MAX_TRADES_PER_RUN", 6)
    # Below this drawdown from the equity peak the team may only reduce risk.
    drawdown_halt_pct: float = _env_float("RISK_DRAWDOWN_HALT_PCT", 0.25)
    # Minimum order size to avoid dust trades.
    min_order_usd: float = _env_float("RISK_MIN_ORDER_USD", 200.0)


# --------------------------------------------------------------------------- #
# Model / runtime
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ModelConfig:
    model: str = os.environ.get("US_TEAM_MODEL", "claude-opus-5")
    analyst_effort: str = os.environ.get("US_TEAM_ANALYST_EFFORT", "medium")
    pm_effort: str = os.environ.get("US_TEAM_PM_EFFORT", "high")
    # Let the news/catalyst analyst use Anthropic's server-side web search.
    web_search: bool = os.environ.get("US_TEAM_WEB_SEARCH", "1") == "1"
    web_search_max_uses: int = _env_int("US_TEAM_WEB_SEARCH_MAX_USES", 6)
    stream: bool = os.environ.get("US_TEAM_STREAM", "1") == "1"


@dataclass(frozen=True)
class RuntimeConfig:
    state_dir: Path = field(default_factory=lambda: Path(os.environ.get("US_TEAM_STATE_DIR", "./state")))
    report_dir: Path = field(default_factory=lambda: Path(os.environ.get("US_TEAM_REPORT_DIR", "./reports/us")))
    history_period: str = os.environ.get("US_TEAM_HISTORY_PERIOD", "6mo")
    history_interval: str = os.environ.get("US_TEAM_HISTORY_INTERVAL", "1d")
    market_open_only: bool = os.environ.get("MARKET_OPEN_ONLY", "0") == "1"


GOAL = GoalConfig()
RISK = RiskLimits()
MODEL = ModelConfig()
RUNTIME = RuntimeConfig()
