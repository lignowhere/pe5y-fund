"""ADV-aware position sizing for portfolio."""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CLOSE_SCALE_VND = 1000.0


@dataclass
class PositionTarget:
    symbol: str
    current_price_vnd: float
    target_value_vnd: float
    target_shares: int
    adv_shares: int
    shares_per_day: int
    days_needed: int
    fill_rate: float
    pe_ratio: Optional[float] = None
    signal_rank: Optional[int] = None


def size_portfolio(
    db_path: Path,
    symbols: list[str],
    capital_vnd: float,
    accum_days: int = 10,
    participation_rate: float = 0.10,
    lot_size: int = 100,
) -> list[PositionTarget]:
    """Size equal-weight portfolio with ADV constraints.

    For each stock:
      target_value = capital / N
      target_shares = floor(target_value / price / lot_size) * lot_size
      shares_per_day = floor(ADV_shares * participation_rate)
      days_needed = ceil(target_shares / shares_per_day)
      fill_rate = min(1.0, shares_per_day * accum_days / target_shares)
    """
    if not symbols or capital_vnd <= 0:
        return []

    n = len(symbols)
    per_stock = capital_vnd / n

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        prices = _query_latest_prices(conn, symbols)
        adv_data = _query_adv_20d(conn, symbols)

    positions: list[PositionTarget] = []
    for sym in symbols:
        price_db = prices.get(sym)
        if not price_db or price_db <= 0:
            continue
        price_vnd = price_db * CLOSE_SCALE_VND
        adv_shares = adv_data.get(sym, 0)

        target_shares = int(math.floor(per_stock / price_vnd / lot_size)) * lot_size
        if target_shares <= 0:
            continue

        spd = int(math.floor(adv_shares * participation_rate))
        if spd <= 0:
            spd = 1

        days_needed = math.ceil(target_shares / spd)
        fillable = min(target_shares, spd * accum_days)
        fill_rate = fillable / target_shares if target_shares > 0 else 0.0

        positions.append(PositionTarget(
            symbol=sym,
            current_price_vnd=price_vnd,
            target_value_vnd=fillable * price_vnd,
            target_shares=target_shares,
            adv_shares=adv_shares,
            shares_per_day=spd,
            days_needed=days_needed,
            fill_rate=round(fill_rate, 4),
        ))

    return positions


def portfolio_summary(positions: list[PositionTarget],
                      capital_vnd: float) -> dict:
    """Compute aggregate portfolio metrics."""
    total_deployed = sum(p.target_value_vnd for p in positions)
    cash_drag = capital_vnd - total_deployed
    avg_fill = (sum(p.fill_rate for p in positions) / len(positions)
                if positions else 0.0)
    max_days = max((p.days_needed for p in positions), default=0)
    return {
        "stock_count": len(positions),
        "total_deployed_vnd": total_deployed,
        "cash_drag_vnd": cash_drag,
        "cash_drag_pct": round(cash_drag / capital_vnd * 100, 2) if capital_vnd else 0,
        "avg_fill_rate": round(avg_fill, 4),
        "max_days_needed": max_days,
    }


# --- SQL queries ---

def _query_latest_prices(conn: sqlite3.Connection,
                         symbols: list[str]) -> dict[str, float]:
    """Latest close per symbol (last 30 trading days)."""
    placeholders = ",".join("?" * len(symbols))
    rows = conn.execute(f"""
        SELECT symbol, close
        FROM stock_price_history
        WHERE symbol IN ({placeholders})
          AND close IS NOT NULL
        GROUP BY symbol
        HAVING time = MAX(time)
    """, symbols).fetchall()
    return {r["symbol"]: float(r["close"]) for r in rows}


def _query_adv_20d(conn: sqlite3.Connection,
                   symbols: list[str]) -> dict[str, int]:
    """20-day average daily volume (shares) per symbol."""
    result: dict[str, int] = {}
    for sym in symbols:
        row = conn.execute("""
            SELECT AVG(volume) as adv
            FROM (
                SELECT volume FROM stock_price_history
                WHERE symbol = ? AND volume IS NOT NULL AND volume > 0
                ORDER BY time DESC LIMIT 20
            )
        """, (sym,)).fetchone()
        if row and row["adv"]:
            result[sym] = int(row["adv"])
    return result
