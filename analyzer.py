"""Technical analysis utilities for Taiwan stock data."""
import pandas as pd
import numpy as np
from typing import Optional


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_macd(close: pd.Series,
                 fast: int = 12, slow: int = 26, signal: int = 9
                 ) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_bollinger(close: pd.Series, period: int = 20,
                      num_std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    ma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = ma + num_std * std
    lower = ma - num_std * std
    return upper, ma, lower


def compute_sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period).mean()


def trend_label(series: pd.Series, window: int = 5) -> str:
    """Return '上漲', '下跌', or '盤整' based on recent slope."""
    if len(series) < window:
        return "資料不足"
    recent = series.dropna().tail(window)
    if len(recent) < 2:
        return "資料不足"
    slope = (recent.iloc[-1] - recent.iloc[0]) / recent.iloc[0] * 100
    if slope > 1.0:
        return "上漲"
    elif slope < -1.0:
        return "下跌"
    return "盤整"


def analyze_symbol(df: pd.DataFrame, symbol: str) -> dict:
    """
    Run full technical analysis on a symbol's OHLCV DataFrame.
    Returns a summary dict suitable for passing to the LLM.
    """
    if df is None or df.empty or len(df) < 5:
        return {"symbol": symbol, "error": "資料不足"}

    close = df["Close"].dropna()
    volume = df["Volume"].dropna()

    if len(close) < 5:
        return {"symbol": symbol, "error": "資料不足"}

    last_price = float(close.iloc[-1])
    prev_price = float(close.iloc[-2]) if len(close) >= 2 else last_price
    change_pct = (last_price - prev_price) / prev_price * 100

    # RSI (need at least 15 points for meaningful result)
    rsi_val: Optional[float] = None
    if len(close) >= 15:
        rsi_series = compute_rsi(close)
        rsi_val = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else None

    # MACD (need at least 27 points)
    macd_val = hist_val = signal_val = None
    if len(close) >= 27:
        macd_line, signal_line, histogram = compute_macd(close)
        macd_val   = float(macd_line.iloc[-1])   if not pd.isna(macd_line.iloc[-1])   else None
        signal_val = float(signal_line.iloc[-1]) if not pd.isna(signal_line.iloc[-1]) else None
        hist_val   = float(histogram.iloc[-1])   if not pd.isna(histogram.iloc[-1])   else None

    # Bollinger Bands
    bb_upper = bb_mid = bb_lower = None
    if len(close) >= 20:
        upper, mid, lower = compute_bollinger(close)
        bb_upper = float(upper.iloc[-1]) if not pd.isna(upper.iloc[-1]) else None
        bb_mid   = float(mid.iloc[-1])   if not pd.isna(mid.iloc[-1])   else None
        bb_lower = float(lower.iloc[-1]) if not pd.isna(lower.iloc[-1]) else None

    # Moving averages
    sma5  = float(compute_sma(close, 5).iloc[-1])  if len(close) >= 5  else None
    sma20 = float(compute_sma(close, 20).iloc[-1]) if len(close) >= 20 else None

    # Volume analysis
    avg_vol = float(volume.mean()) if len(volume) > 0 else 0
    last_vol = float(volume.iloc[-1]) if len(volume) > 0 else 0
    vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1.0

    # Price range over period
    period_high = float(close.max())
    period_low  = float(close.min())
    dist_from_high = (last_price - period_high) / period_high * 100
    dist_from_low  = (last_price - period_low)  / period_low  * 100

    # Trend
    trend = trend_label(close)

    # Signals
    signals = []
    if rsi_val is not None:
        if rsi_val > 70:
            signals.append("RSI超買 (>70)")
        elif rsi_val < 30:
            signals.append("RSI超賣 (<30)")
    if hist_val is not None and macd_val is not None and signal_val is not None:
        if macd_val > signal_val and hist_val > 0:
            signals.append("MACD黃金交叉")
        elif macd_val < signal_val and hist_val < 0:
            signals.append("MACD死亡交叉")
    if bb_upper and bb_lower:
        if last_price > bb_upper:
            signals.append("突破布林上軌")
        elif last_price < bb_lower:
            signals.append("跌破布林下軌")
    if vol_ratio > 2.0:
        signals.append(f"爆量 ({vol_ratio:.1f}x均量)")
    if sma5 and sma20:
        if sma5 > sma20:
            signals.append("MA5 > MA20 (多頭排列)")
        else:
            signals.append("MA5 < MA20 (空頭排列)")

    return {
        "symbol": symbol,
        "last_price": round(last_price, 2),
        "change_pct": round(change_pct, 2),
        "trend": trend,
        "rsi": round(rsi_val, 1) if rsi_val is not None else None,
        "macd": round(macd_val, 4) if macd_val is not None else None,
        "macd_signal": round(signal_val, 4) if signal_val is not None else None,
        "macd_hist": round(hist_val, 4) if hist_val is not None else None,
        "bb_upper": round(bb_upper, 2) if bb_upper else None,
        "bb_mid":   round(bb_mid, 2)   if bb_mid   else None,
        "bb_lower": round(bb_lower, 2) if bb_lower else None,
        "sma5":  round(sma5, 2)  if sma5  else None,
        "sma20": round(sma20, 2) if sma20 else None,
        "period_high": round(period_high, 2),
        "period_low":  round(period_low, 2),
        "dist_from_high_pct": round(dist_from_high, 2),
        "dist_from_low_pct": round(dist_from_low, 2),
        "volume_ratio": round(vol_ratio, 2),
        "signals": signals,
        "data_points": len(close),
    }


def build_market_summary(analyses: dict[str, dict]) -> dict:
    """Aggregate per-symbol analyses into a market-level summary."""
    gainers = []
    losers  = []
    overbought = []
    oversold   = []
    high_volume = []

    for sym, a in analyses.items():
        if "error" in a:
            continue
        cp = a.get("change_pct", 0)
        if cp > 0:
            gainers.append((sym, cp))
        else:
            losers.append((sym, cp))

        rsi = a.get("rsi")
        if rsi is not None:
            if rsi > 70:
                overbought.append((sym, rsi))
            elif rsi < 30:
                oversold.append((sym, rsi))

        if a.get("volume_ratio", 1) > 1.5:
            high_volume.append((sym, a["volume_ratio"]))

    gainers.sort(key=lambda x: x[1], reverse=True)
    losers.sort(key=lambda x: x[1])
    overbought.sort(key=lambda x: x[1], reverse=True)
    oversold.sort(key=lambda x: x[1])
    high_volume.sort(key=lambda x: x[1], reverse=True)

    return {
        "top_gainers":    gainers[:5],
        "top_losers":     losers[:5],
        "overbought":     overbought[:5],
        "oversold":       oversold[:5],
        "high_volume":    high_volume[:5],
        "total_analyzed": len([a for a in analyses.values() if "error" not in a]),
    }
