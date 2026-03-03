"""Benchmark index returns calculator."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def calc_benchmark_cagr(
    db_path: Path,
    symbol: str = "VNINDEX",
    rebalance_month: int = 9,
    start_year: int = 2015,
    end_year: int = 2025,
    max_gap_days: int = 10,
) -> Optional[float]:
    """Calculate buy-and-hold CAGR for a benchmark index.

    Note: Index prices in DB are stored as raw points (not thousands),
    unlike stock prices which use close_scale_vnd=1000.

    Returns CAGR as percentage (e.g. 12.5 for 12.5%), or None if data missing.
    """
    years = end_year - start_year
    if years <= 0:
        return None

    start_date = f"{start_year}-{rebalance_month:02d}-01"
    end_date = f"{end_year}-{rebalance_month:02d}-01"

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row

        start_price = _get_price_on_or_after(conn, symbol, start_date, max_gap_days)
        end_price = _get_price_on_or_after(conn, symbol, end_date, max_gap_days)

        if not start_price or not end_price or start_price <= 0:
            log.warning("Benchmark %s: missing price data for %s..%s", symbol, start_date, end_date)
            return None

        cagr = (end_price / start_price) ** (1 / years) - 1
        log.info(
            "Benchmark %s CAGR: %.2f%% (%s=%.1f → %s=%.1f, %dy)",
            symbol, cagr * 100, start_date, start_price, end_date, end_price, years,
        )
        return round(cagr * 100, 2)


def _get_price_on_or_after(
    conn: sqlite3.Connection,
    symbol: str,
    date: str,
    max_gap_days: int = 10,
) -> Optional[float]:
    """Get first available close price on or after date, within gap days."""
    row = conn.execute(
        """SELECT close FROM stock_price_history
           WHERE symbol = ? AND time >= ? AND time <= date(?, '+' || ? || ' days')
           ORDER BY time LIMIT 1""",
        (symbol, date, date, str(max_gap_days)),
    ).fetchone()
    return float(row["close"]) if row else None
