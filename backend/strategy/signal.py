"""PE5Y signal generation — select lowest P/E stocks by 5-year avg EPS."""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import AppConfig
from .market_cap_filter import get_min_market_cap

CLOSE_SCALE_VND = 1000.0  # DB stores close in thousands


@dataclass
class PE5YCandidate:
    symbol: str
    avg_eps_5y: float
    eps_current: float
    pe_5y_avg: float
    market_cap_vnd: float
    signal_rank: int
    buy_price_vnd: Optional[float] = None


def generate_signal(
    db_path: Path,
    formation_year: int,
    config: AppConfig,
    *,
    hold_year: Optional[int] = None,
    rebalance_date: Optional[str] = None,
) -> list[PE5YCandidate]:
    """Generate PE5Y ranked candidate list for a formation year.

    Steps:
      1. Query 5-year annual EPS (all positive, min years varies by hold_year)
      2. Apply market cap filter (dynamic by year)
      3. Apply liquidity filters (trading days, ADV, zero volume)
      4. Compute pe_5y_avg = price_vnd / avg_eps_5y
      5. Rank by pe_5y_avg ascending

    When rebalance_date is given, PE is computed using the first traded price
    on or after that date (matching original backtest logic).
    Otherwise uses formation year end price.
    """
    if hold_year is None:
        hold_year = formation_year + 1

    # EPS min years: 5 for hold_year >= 2018, relaxed to 3 for 2015-2017
    eps_min_years = _eps_min_years_for_hold_year(hold_year)

    sc = config.strategy
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        # 1) EPS candidates
        eps_df = _query_eps_candidates(conn, formation_year, eps_min_years)
        if not eps_df:
            return []

        # 2) Market cap filter
        min_mcap = get_min_market_cap(
            formation_year, base_vnd=sc.mcap_base_vnd,
            growth_rate=sc.mcap_growth_rate,
            growth_period=sc.mcap_growth_period_years,
            base_year=sc.mcap_base_year,
        )
        mcap = _query_market_cap(conn, formation_year)
        eps_df = [e for e in eps_df if mcap.get(e["symbol"], 0) >= min_mcap]

        # 3) Liquidity filters
        liq = _query_liquidity_stats(conn, formation_year, sc)
        eps_df = [e for e in eps_df if e["symbol"] in liq]

        # 4) Get price for PE calculation
        if rebalance_date:
            prices = _query_price_on_or_after(conn, rebalance_date)
        else:
            prices = _query_latest_close(conn, formation_year)

        # 5) Compute PE5Y and rank
        candidates: list[PE5YCandidate] = []
        for e in eps_df:
            sym = e["symbol"]
            close = prices.get(sym)
            if not close or close <= 0:
                continue
            buy_vnd = close * CLOSE_SCALE_VND
            pe5y = buy_vnd / e["avg_eps"]
            if pe5y <= 1.0 or not math.isfinite(pe5y):
                continue
            candidates.append(PE5YCandidate(
                symbol=sym,
                avg_eps_5y=e["avg_eps"],
                eps_current=e["eps_current"],
                pe_5y_avg=pe5y,
                market_cap_vnd=mcap.get(sym, 0),
                signal_rank=0,
                buy_price_vnd=buy_vnd,
            ))

        candidates.sort(key=lambda c: c.pe_5y_avg)
        for i, c in enumerate(candidates):
            c.signal_rank = i + 1
        return candidates


def select_top_n(candidates: list[PE5YCandidate], select_pct: float,
                 min_holdings: int = 10) -> list[PE5YCandidate]:
    """Select top N candidates by PE5Y rank. N = ceil(universe * pct/100)."""
    n = len(candidates)
    if n == 0:
        return []
    target = max(min_holdings, math.ceil(n * select_pct / 100.0))
    return candidates[:target]


# --- EPS min years logic ---

def _eps_min_years_for_hold_year(hold_year: int) -> int:
    """Return required EPS years: 5 for 2018+, relaxed to 3 for 2015-2017."""
    if 2015 <= hold_year <= 2017:
        return 3
    return 5


# --- SQL queries ---

def _query_eps_candidates(conn: sqlite3.Connection,
                          formation_year: int,
                          min_eps_years: int = 5) -> list[dict]:
    """5-year annual EPS: all positive, >= min_eps_years available.

    Uses GROUP BY + AVG to handle potential duplicate rows.
    """
    y0 = formation_year - 4
    rows = conn.execute("""
        SELECT symbol, year, AVG(eps_vnd) as eps
        FROM financial_ratios
        WHERE year >= ? AND year <= ? AND eps_vnd IS NOT NULL
          AND quarter IS NULL
        GROUP BY symbol, year
        ORDER BY symbol, year
    """, (y0, formation_year)).fetchall()

    from collections import defaultdict
    by_sym: dict[str, list] = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append({"year": r["year"], "eps": float(r["eps"])})

    result = []
    for sym, entries in by_sym.items():
        if any(e["eps"] <= 0 for e in entries):
            continue
        if len(entries) < min_eps_years:
            continue
        avg_eps = sum(e["eps"] for e in entries) / len(entries)
        current = [e for e in entries if e["year"] == formation_year]
        eps_current = current[0]["eps"] if current else entries[-1]["eps"]
        if eps_current <= 0 or avg_eps <= 0:
            continue
        result.append({"symbol": sym, "avg_eps": avg_eps,
                       "eps_current": eps_current, "years": len(entries)})
    return result


def _query_market_cap(conn: sqlite3.Connection,
                      formation_year: int) -> dict[str, float]:
    """Market cap from financial_ratios (closest year).

    Uses GROUP BY + AVG to handle potential duplicate rows.
    """
    rows = conn.execute("""
        SELECT symbol, AVG(market_cap_billions) as market_cap_vnd
        FROM financial_ratios
        WHERE year = ? AND market_cap_billions IS NOT NULL
          AND quarter IS NULL
        GROUP BY symbol
    """, (formation_year,)).fetchall()
    # Column name is misleading — values are actually in VND
    return {r["symbol"]: float(r["market_cap_vnd"]) for r in rows}


def _query_liquidity_stats(conn: sqlite3.Connection,
                           formation_year: int,
                           sc) -> set[str]:
    """Return symbols passing liquidity filters in formation year."""
    rows = conn.execute("""
        SELECT symbol, time, close, volume
        FROM stock_price_history
        WHERE time >= ? AND time < ?
          AND close IS NOT NULL
        ORDER BY symbol, time
    """, (f"{formation_year}-01-01", f"{formation_year + 1}-01-01")).fetchall()

    from collections import defaultdict
    by_sym: dict[str, list] = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append({
            "close": float(r["close"]), "volume": int(r["volume"] or 0)
        })

    passing = set()
    for sym, bars in by_sym.items():
        n = len(bars)
        if n < sc.min_trading_days:
            continue
        zero_vol = sum(1 for b in bars if b["volume"] == 0)
        if zero_vol / n > sc.max_zero_volume_frac:
            continue
        closes = [b["close"] for b in bars]
        stale = sum(1 for i in range(1, len(closes)) if closes[i] == closes[i - 1])
        if stale / max(n - 1, 1) > sc.max_stale_close_frac:
            continue
        adv = sum(b["close"] * CLOSE_SCALE_VND * b["volume"] for b in bars) / n
        if adv < sc.min_avg_dollar_volume_vnd:
            continue
        passing.add(sym)
    return passing


def _query_latest_close(conn: sqlite3.Connection,
                        formation_year: int) -> dict[str, float]:
    """Latest close price per symbol in formation year (in DB scale)."""
    rows = conn.execute("""
        SELECT symbol, close
        FROM stock_price_history
        WHERE time >= ? AND time < ?
          AND close IS NOT NULL
        GROUP BY symbol
        HAVING time = MAX(time)
    """, (f"{formation_year}-01-01", f"{formation_year + 1}-01-01")).fetchall()
    return {r["symbol"]: float(r["close"]) for r in rows}


def _query_price_on_or_after(conn: sqlite3.Connection,
                              target_date: str,
                              max_gap_days: int = 10) -> dict[str, float]:
    """First traded close price on or after target_date, within max_gap_days.

    Matches original backtest's forward-looking rebalance price logic.
    Returns prices in DB scale (close / 1000).
    """
    from datetime import datetime, timedelta
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    end_dt = dt + timedelta(days=max_gap_days)
    end_date = end_dt.strftime("%Y-%m-%d")

    # Use subquery to get first traded date per symbol, then join for close
    rows = conn.execute("""
        SELECT h.symbol, h.close
        FROM stock_price_history h
        INNER JOIN (
            SELECT symbol, MIN(time) as first_time
            FROM stock_price_history
            WHERE time >= ? AND time <= ? AND close IS NOT NULL
            GROUP BY symbol
        ) sub ON h.symbol = sub.symbol AND h.time = sub.first_time
        WHERE h.close IS NOT NULL
    """, (target_date, end_date)).fetchall()
    return {r["symbol"]: float(r["close"]) for r in rows}
