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
    buy_price_vnd: Optional[float] = None
    gain_pct: Optional[float] = None


def size_portfolio(
    db_path: Path,
    symbols: list[str],
    capital_vnd: float,
    accum_days: int = 10,
    participation_rate: float = 0.10,
    lot_size: int = 100,
    buy_prices: dict[str, float] | None = None,
    price_date: str | None = None,
) -> list[PositionTarget]:
    """Size equal-weight portfolio with ADV constraints.

    For each stock:
      target_value = capital / N
      target_shares = floor(target_value / buy_price / lot_size) * lot_size
      shares_per_day = floor(ADV_shares * participation_rate)
      days_needed = ceil(target_shares / shares_per_day)
      fill_rate = min(1.0, shares_per_day * accum_days / target_shares)

    buy_prices: if provided, use rebalance-date prices (VND) for sizing;
                current prices are still queried for display (current_price_vnd).
    """
    if not symbols or capital_vnd <= 0:
        return []

    n = len(symbols)
    per_stock = capital_vnd / n

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        prices = _query_latest_prices(conn, symbols)
        adv_data = (query_adv_20d_historical(conn, symbols, price_date)
                    if price_date else _query_adv_20d(conn, symbols))

    positions: list[PositionTarget] = []
    for sym in symbols:
        price_db = prices.get(sym)
        if not price_db or price_db <= 0:
            continue
        current_price_vnd = price_db * CLOSE_SCALE_VND
        sizing_price_vnd = (buy_prices.get(sym) or current_price_vnd) if buy_prices else current_price_vnd
        adv_shares = adv_data.get(sym, 0)

        target_shares = int(math.floor(per_stock / sizing_price_vnd / lot_size)) * lot_size
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
            current_price_vnd=current_price_vnd,
            target_value_vnd=fillable * sizing_price_vnd,
            target_shares=target_shares,
            adv_shares=adv_shares,
            shares_per_day=spd,
            days_needed=days_needed,
            fill_rate=round(fill_rate, 4),
        ))

    return positions


def portfolio_summary(positions: list[PositionTarget],
                      capital_vnd: float) -> dict:
    """Compute aggregate portfolio metrics incl. gain vs buy price."""
    total_deployed = sum(p.target_value_vnd for p in positions)
    cash_drag = capital_vnd - total_deployed
    avg_fill = (sum(p.fill_rate for p in positions) / len(positions)
                if positions else 0.0)
    max_days = max((p.days_needed for p in positions), default=0)

    # Portfolio-level gain: weighted by deployed value
    positions_with_gain = [p for p in positions if p.buy_price_vnd and p.gain_pct is not None]
    if positions_with_gain:
        buy_total = sum(p.buy_price_vnd * p.target_shares
                        for p in positions_with_gain if p.buy_price_vnd)
        cur_total = sum(p.current_price_vnd * p.target_shares
                        for p in positions_with_gain)
        portfolio_gain_pct = round((cur_total / buy_total - 1) * 100, 2) if buy_total else None
        portfolio_gain_vnd = round(cur_total - buy_total)
        gainers = sum(1 for p in positions_with_gain if (p.gain_pct or 0) > 0)
        losers = sum(1 for p in positions_with_gain if (p.gain_pct or 0) < 0)
    else:
        portfolio_gain_pct = None
        portfolio_gain_vnd = None
        gainers = 0
        losers = 0

    return {
        "stock_count": len(positions),
        "total_deployed_vnd": total_deployed,
        "cash_drag_vnd": cash_drag,
        "cash_drag_pct": round(cash_drag / capital_vnd * 100, 2) if capital_vnd else 0,
        "avg_fill_rate": round(avg_fill, 4),
        "max_days_needed": max_days,
        "portfolio_gain_pct": portfolio_gain_pct,
        "portfolio_gain_vnd": portfolio_gain_vnd,
        "gainers": gainers,
        "losers": losers,
    }


# --- SQL queries ---

def _query_latest_prices(conn: sqlite3.Connection,
                         symbols: list[str]) -> dict[str, float]:
    """Latest close per symbol (deterministic — uses subquery for MAX time)."""
    if not symbols:
        return {}
    placeholders = ",".join("?" * len(symbols))
    rows = conn.execute(f"""
        SELECT h.symbol, h.close
        FROM stock_price_history h
        INNER JOIN (
            SELECT symbol, MAX(time) AS max_time
            FROM stock_price_history
            WHERE symbol IN ({placeholders})
              AND close IS NOT NULL
            GROUP BY symbol
        ) sub ON h.symbol = sub.symbol AND h.time = sub.max_time
        WHERE h.close IS NOT NULL
    """, symbols).fetchall()
    return {r["symbol"]: float(r["close"]) for r in rows}


def query_latest_price_date(db_path: Path) -> str | None:
    """Return the most recent trading date with broad coverage.

    Picks the latest date that has >= 100 symbols with price data,
    avoiding partial-update dates where only a few symbols are fresh.
    """
    with sqlite3.connect(str(db_path)) as conn:
        has_metadata = conn.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = 'market_price_metadata'"""
        ).fetchone()
        if has_metadata:
            has_observations = conn.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type = 'table'
                     AND name = 'price_source_observations'"""
            ).fetchone()
            observation_clause = (
                """
                  AND EXISTS (
                    SELECT 1
                    FROM price_source_observations observed
                    WHERE observed.symbol = h.symbol
                      AND observed.price_date = h.time
                      AND observed.source = m.source
                      AND observed.payload_sha256 =
                          m.source_payload_sha256
                      AND observed.is_session_final = 1
                      AND observed.verification_status = 'verified'
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM price_source_observations conflict
                    WHERE conflict.symbol = h.symbol
                      AND conflict.price_date = h.time
                      AND conflict.verification_status = 'conflict'
                  )
                """
                if has_observations
                else ""
            )
            row = conn.execute(
                f"""
                SELECT h.time
                FROM stock_price_history h
                LEFT JOIN market_price_metadata m
                  ON m.symbol = h.symbol AND m.price_date = h.time
                WHERE h.close IS NOT NULL
                  AND h.time >= date('now', '-45 days')
                  AND m.is_provisional = 0
                  AND m.source <> 'LEGACY_UNKNOWN'
                  AND m.source_url IS NOT NULL
                  AND m.source_url <> ''
                  AND m.raw_unit = 'THOUSAND_VND'
                  AND m.price_basis IN (
                    'current_spot', 'execution_unadjusted'
                  )
                  AND LENGTH(m.source_payload_sha256) = 64
                  {observation_clause}
                GROUP BY h.time
                HAVING COUNT(DISTINCT h.symbol) >= 100
                ORDER BY h.time DESC LIMIT 1
                """
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT time FROM stock_price_history
                WHERE close IS NOT NULL
                  AND time >= date('now', '-45 days')
                GROUP BY time
                HAVING COUNT(DISTINCT symbol) >= 100
                ORDER BY time DESC LIMIT 1
                """
            ).fetchone()
        return row[0] if row and row[0] else None


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


def query_adv_20d_historical(conn: sqlite3.Connection,
                              symbols: list[str],
                              as_of_date: str) -> dict[str, int]:
    """20-day average daily volume (shares) as of a historical date.

    Uses 20 trading days strictly before *as_of_date* to avoid look-ahead bias.
    """
    result: dict[str, int] = {}
    for sym in symbols:
        row = conn.execute("""
            SELECT AVG(volume) as adv
            FROM (
                SELECT volume FROM stock_price_history
                WHERE symbol = ? AND volume IS NOT NULL AND volume > 0
                  AND time < ?
                ORDER BY time DESC LIMIT 20
            )
        """, (sym, as_of_date)).fetchone()
        if row and row["adv"]:
            result[sym] = int(row["adv"])
    return result
