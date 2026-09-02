"""Paper portfolio with JSON persistence.

This is a simulation ledger, not a broker connection. Fills happen at the
latest price the data layer provides. To connect a real broker, implement
`execute_orders` in your own module and call it with the `TradeOrder`s the
risk engine emits, then mirror the fills here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path


@dataclass
class Position:
    symbol: str
    shares: float
    avg_cost: float
    stop_loss: float | None = None
    opened_at: str = ""
    thesis: str = ""

    def market_value(self, price: float) -> float:
        return self.shares * price

    def pnl_pct(self, price: float) -> float:
        return (price / self.avg_cost - 1.0) * 100 if self.avg_cost else 0.0


@dataclass
class Trade:
    timestamp: str
    symbol: str
    side: str            # BUY | SELL
    shares: float
    price: float
    value: float
    reason: str
    realized_pnl: float = 0.0


@dataclass
class Portfolio:
    cash: float
    start_capital: float
    start_date: str
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    equity_history: list[dict] = field(default_factory=list)
    peak_equity: float = 0.0
    realized_pnl: float = 0.0

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    @classmethod
    def load_or_create(cls, path: Path, start_capital: float,
                       start_date: str | None = None) -> "Portfolio":
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            pf = cls(
                cash=raw["cash"],
                start_capital=raw["start_capital"],
                start_date=raw["start_date"],
                positions={s: Position(**p) for s, p in raw.get("positions", {}).items()},
                trades=[Trade(**t) for t in raw.get("trades", [])],
                equity_history=raw.get("equity_history", []),
                peak_equity=raw.get("peak_equity", raw["start_capital"]),
                realized_pnl=raw.get("realized_pnl", 0.0),
            )
            return pf
        pf = cls(
            cash=start_capital,
            start_capital=start_capital,
            start_date=start_date or datetime.now().date().isoformat(),
            peak_equity=start_capital,
        )
        pf.save(path)
        return pf

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "cash": self.cash,
            "start_capital": self.start_capital,
            "start_date": self.start_date,
            "positions": {s: asdict(p) for s, p in self.positions.items()},
            "trades": [asdict(t) for t in self.trades],
            "equity_history": self.equity_history[-2000:],
            "peak_equity": self.peak_equity,
            "realized_pnl": self.realized_pnl,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    # ------------------------------------------------------------------ #
    # Valuation
    # ------------------------------------------------------------------ #
    def equity(self, prices: dict[str, float]) -> float:
        value = self.cash
        for sym, pos in self.positions.items():
            price = prices.get(sym, pos.avg_cost)
            value += pos.market_value(price)
        return value

    def gross_exposure(self, prices: dict[str, float]) -> float:
        eq = self.equity(prices)
        if eq <= 0:
            return 0.0
        invested = sum(p.market_value(prices.get(s, p.avg_cost)) for s, p in self.positions.items())
        return invested / eq

    def weights(self, prices: dict[str, float]) -> dict[str, float]:
        eq = self.equity(prices)
        if eq <= 0:
            return {}
        return {s: p.market_value(prices.get(s, p.avg_cost)) / eq for s, p in self.positions.items()}

    def drawdown(self, prices: dict[str, float]) -> float:
        eq = self.equity(prices)
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, 1.0 - eq / self.peak_equity)

    def mark(self, prices: dict[str, float], when: datetime | None = None) -> float:
        """Record an equity snapshot and update the peak. Returns equity."""
        eq = self.equity(prices)
        self.peak_equity = max(self.peak_equity, eq)
        ts = (when or datetime.now()).isoformat(timespec="seconds")
        self.equity_history.append({"timestamp": ts, "equity": round(eq, 2), "cash": round(self.cash, 2)})
        return eq

    # ------------------------------------------------------------------ #
    # Execution (paper fills)
    # ------------------------------------------------------------------ #
    def buy(self, symbol: str, shares: float, price: float, reason: str = "",
            stop_loss: float | None = None, thesis: str = "",
            when: datetime | None = None) -> Trade:
        if shares <= 0 or price <= 0:
            raise ValueError("shares and price must be positive")
        cost = shares * price
        if cost > self.cash + 1e-6:
            raise ValueError(f"insufficient cash: need {cost:.2f}, have {self.cash:.2f}")
        self.cash -= cost
        ts = (when or datetime.now()).isoformat(timespec="seconds")
        pos = self.positions.get(symbol)
        if pos:
            total_shares = pos.shares + shares
            pos.avg_cost = (pos.avg_cost * pos.shares + cost) / total_shares
            pos.shares = total_shares
            if stop_loss is not None:
                pos.stop_loss = stop_loss
            if thesis:
                pos.thesis = thesis
        else:
            self.positions[symbol] = Position(
                symbol=symbol, shares=shares, avg_cost=price,
                stop_loss=stop_loss, opened_at=ts, thesis=thesis,
            )
        trade = Trade(ts, symbol, "BUY", shares, price, cost, reason)
        self.trades.append(trade)
        return trade

    def sell(self, symbol: str, shares: float, price: float, reason: str = "",
             when: datetime | None = None) -> Trade:
        pos = self.positions.get(symbol)
        if not pos:
            raise ValueError(f"no position in {symbol}")
        if shares <= 0 or price <= 0:
            raise ValueError("shares and price must be positive")
        shares = min(shares, pos.shares)
        proceeds = shares * price
        realized = (price - pos.avg_cost) * shares
        self.cash += proceeds
        self.realized_pnl += realized
        pos.shares -= shares
        if pos.shares <= 1e-9:
            del self.positions[symbol]
        ts = (when or datetime.now()).isoformat(timespec="seconds")
        trade = Trade(ts, symbol, "SELL", shares, price, proceeds, reason, realized_pnl=realized)
        self.trades.append(trade)
        return trade

    def check_stops(self, prices: dict[str, float], when: datetime | None = None) -> list[Trade]:
        """Sell any position whose price is at or below its stop. Returns fills."""
        fills = []
        for sym in list(self.positions):
            pos = self.positions[sym]
            price = prices.get(sym)
            if price is None or pos.stop_loss is None:
                continue
            if price <= pos.stop_loss:
                fills.append(self.sell(sym, pos.shares, price,
                                       reason=f"停損觸發 (stop {pos.stop_loss:.2f})", when=when))
        return fills

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #
    def summary(self, prices: dict[str, float]) -> dict:
        eq = self.equity(prices)
        rows = []
        for sym, pos in self.positions.items():
            price = prices.get(sym, pos.avg_cost)
            rows.append({
                "symbol": sym,
                "shares": round(pos.shares, 4),
                "avg_cost": round(pos.avg_cost, 2),
                "price": round(price, 2),
                "value": round(pos.market_value(price), 2),
                "weight_pct": round(pos.market_value(price) / eq * 100, 2) if eq else 0.0,
                "pnl_pct": round(pos.pnl_pct(price), 2),
                "stop_loss": round(pos.stop_loss, 2) if pos.stop_loss else None,
                "opened_at": pos.opened_at,
                "thesis": pos.thesis,
            })
        rows.sort(key=lambda r: r["value"], reverse=True)
        return {
            "equity": round(eq, 2),
            "cash": round(self.cash, 2),
            "cash_pct": round(self.cash / eq * 100, 2) if eq else 100.0,
            "gross_exposure_pct": round(self.gross_exposure(prices) * 100, 2),
            "total_return_pct": (round((eq / self.start_capital - 1) * 100, 2) or 0.0) if self.start_capital else 0.0,
            "realized_pnl": round(self.realized_pnl, 2),
            "peak_equity": round(self.peak_equity, 2),
            "drawdown_pct": round(self.drawdown(prices) * 100, 2),
            "n_positions": len(self.positions),
            "n_trades": len(self.trades),
            "positions": rows,
            "recent_trades": [asdict(t) for t in self.trades[-10:]],
        }


def describe_portfolio(summary: dict) -> str:
    """Traditional Chinese text block for prompts."""
    lines = [
        f"- 總權益: ${summary['equity']:,.2f}｜現金: ${summary['cash']:,.2f} ({summary['cash_pct']:.1f}%)"
        f"｜總曝險 {summary['gross_exposure_pct']:.1f}%",
        f"- 累計報酬: {summary['total_return_pct']:+.2f}%｜已實現損益: ${summary['realized_pnl']:,.2f}"
        f"｜峰值權益 ${summary['peak_equity']:,.2f}｜目前回撤 {summary['drawdown_pct']:.1f}%",
        f"- 持股數: {summary['n_positions']}｜歷史交易數: {summary['n_trades']}",
    ]
    if summary["positions"]:
        lines.append("- 目前持股:")
        for r in summary["positions"]:
            stop = f" 停損 {r['stop_loss']}" if r["stop_loss"] else ""
            lines.append(
                f"  - {r['symbol']}: {r['shares']} 股 @ 成本 {r['avg_cost']}，現價 {r['price']}，"
                f"權重 {r['weight_pct']:.1f}%，損益 {r['pnl_pct']:+.1f}%{stop}"
                + (f"｜論點: {r['thesis'][:80]}" if r["thesis"] else "")
            )
    else:
        lines.append("- 目前無持股（全現金）")
    if summary["recent_trades"]:
        lines.append("- 最近交易:")
        for t in summary["recent_trades"][-5:]:
            lines.append(f"  - {t['timestamp'][:16]} {t['side']} {t['symbol']} {t['shares']} @ {t['price']:.2f} ({t['reason']})")
    return "\n".join(lines)
