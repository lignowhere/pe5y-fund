"""PE_TTM_20Q signal generation — select lowest P/E stocks by 20-quarter avg EPS."""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import AppConfig
from .market_cap_filter import get_min_market_cap

CLOSE_SCALE_VND = 1000.0  # DB stores close in thousands
MIN_QUARTERS = 20
# Reporting lag: ~2 months after quarter end
_LATEST_Q_BY_MONTH = {
    1: (-1, 3), 2: (-1, 3),    # Q3 prev year
    3: (-1, 4), 4: (-1, 4),    # Q4 prev year
    5: (0, 1), 6: (0, 1), 7: (0, 1),  # Q1 current year
    8: (0, 2), 9: (0, 2), 10: (0, 2),  # Q2 current year
    11: (0, 3), 12: (0, 3),    # Q3 current year
}


@dataclass
class PE20QCandidate:
    symbol: str
    avg_eps_20q: float
    pe_ttm_20q: float
    market_cap_vnd: float
    signal_rank: int
    buy_price_vnd: Optional[float] = None
    quarters_count: int = 0


def generate_signal_20q(
    db_path: Path,
    formation_year: int,
    config: AppConfig,
    *,
    hold_year: Optional[int] = None,
    rebalance_date: Optional[str] = None,
    rebalance_month: int = 9,
    require_all_positive: bool = True,
    ktpl_leakage: Optional[dict[str, float]] = None,
) -> list[PE20QCandidate]:
    """Generate PE_TTM_20Q ranked candidate list.

    Uses 20 quarters of quarterly EPS instead of 5 years of annual EPS.
    Same market cap and liquidity filters as PE5Y.

    When require_all_positive=False, only requires avg EPS > 0 (relaxed filter).
    When ktpl_leakage is provided, adjusts EPS: adj_eps = eps * (1 - ratio).
    """
    if hold_year is None:
        hold_year = formation_year + 1

    sc = config.strategy
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row

        # 1) Quarterly EPS candidates
        latest_y, latest_q = _latest_quarter(hold_year, rebalance_month)
        eps_df = _query_quarterly_eps(conn, latest_y, latest_q,
                                      require_all_positive=require_all_positive)
        if not eps_df:
            return []

        # 1b) Apply KTPL adjustment if provided
        if ktpl_leakage:
            for e in eps_df:
                ratio = ktpl_leakage.get(e["symbol"], 0.0)
                e["avg_eps"] = e["avg_eps"] * (1.0 - ratio)
            eps_df = [e for e in eps_df if e["avg_eps"] > 0]

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

        # 5) Compute PE and rank
        candidates: list[PE20QCandidate] = []
        for e in eps_df:
            sym = e["symbol"]
            close = prices.get(sym)
            if not close or close <= 0:
                continue
            buy_vnd = close * CLOSE_SCALE_VND
            pe = buy_vnd / e["avg_eps"]
            if pe <= 1.0 or not math.isfinite(pe):
                continue
            candidates.append(PE20QCandidate(
                symbol=sym, avg_eps_20q=e["avg_eps"],
                pe_ttm_20q=pe, market_cap_vnd=mcap.get(sym, 0),
                signal_rank=0, buy_price_vnd=buy_vnd,
                quarters_count=e["cnt"],
            ))

        candidates.sort(key=lambda c: c.pe_ttm_20q)
        for i, c in enumerate(candidates):
            c.signal_rank = i + 1
        return candidates


def select_top_n_20q(
    candidates: list[PE20QCandidate], select_pct: float, min_holdings: int = 10,
) -> list[PE20QCandidate]:
    n = len(candidates)
    if n == 0:
        return []
    target = max(min_holdings, math.ceil(n * select_pct / 100.0))
    return candidates[:target]


def _latest_quarter(hold_year: int, month: int) -> tuple[int, int]:
    """Return (year, quarter) of the latest reported quarter at rebalance."""
    y_offset, q = _LATEST_Q_BY_MONTH.get(month, (0, 2))
    return hold_year + y_offset, q


def _quarter_key(year: int, quarter: int) -> int:
    return year * 10 + quarter


# --- SQL queries (parallel to signal.py) ---

def _query_quarterly_eps(
    conn: sqlite3.Connection, end_year: int, end_quarter: int,
    *, require_all_positive: bool = True,
) -> list[dict]:
    """20 quarters of quarterly EPS.

    require_all_positive=True: all 20 quarters must have EPS > 0 (strict).
    require_all_positive=False: only avg EPS > 0 required (relaxed).
    """
    end_key = _quarter_key(end_year, end_quarter)
    # 20 quarters back: 5 years back, same quarter + 1
    start_year = end_year - 5
    start_quarter = end_quarter + 1
    if start_quarter > 4:
        start_quarter = 1
        start_year += 1
    start_key = _quarter_key(start_year, start_quarter)

    rows = conn.execute("""
        SELECT symbol, year, quarter, AVG(eps_vnd) as eps
        FROM financial_ratios
        WHERE quarter IS NOT NULL AND quarter <= 4
          AND eps_vnd IS NOT NULL
          AND (year * 10 + quarter) >= ? AND (year * 10 + quarter) <= ?
        GROUP BY symbol, year, quarter
        ORDER BY symbol, year, quarter
    """, (start_key, end_key)).fetchall()

    from collections import defaultdict
    by_sym: dict[str, list] = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append(float(r["eps"]))

    result = []
    for sym, eps_list in by_sym.items():
        if len(eps_list) < MIN_QUARTERS:
            continue
        if require_all_positive and any(e <= 0 for e in eps_list):
            continue
        avg_eps = sum(eps_list) / len(eps_list)
        if avg_eps <= 0:
            continue
        result.append({"symbol": sym, "avg_eps": avg_eps, "cnt": len(eps_list)})
    return result


def _query_market_cap(conn: sqlite3.Connection, year: int) -> dict[str, float]:
    rows = conn.execute("""
        SELECT symbol, AVG(market_cap_billions) as mcap
        FROM financial_ratios
        WHERE year = ? AND market_cap_billions IS NOT NULL AND quarter IS NULL
        GROUP BY symbol
    """, (year,)).fetchall()
    return {r["symbol"]: float(r["mcap"]) for r in rows}


def _query_liquidity_stats(conn: sqlite3.Connection, year: int, sc) -> set[str]:
    rows = conn.execute("""
        SELECT symbol, time, close, volume FROM stock_price_history
        WHERE time >= ? AND time < ? AND close IS NOT NULL
        ORDER BY symbol, time
    """, (f"{year}-01-01", f"{year + 1}-01-01")).fetchall()

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
        if sum(1 for b in bars if b["volume"] == 0) / n > sc.max_zero_volume_frac:
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


def _query_price_on_or_after(conn: sqlite3.Connection, target_date: str,
                              max_gap_days: int = 10) -> dict[str, float]:
    from datetime import datetime, timedelta
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    end_date = (dt + timedelta(days=max_gap_days)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT h.symbol, h.close FROM stock_price_history h
        INNER JOIN (
            SELECT symbol, MIN(time) as first_time FROM stock_price_history
            WHERE time >= ? AND time <= ? AND close IS NOT NULL
            GROUP BY symbol
        ) sub ON h.symbol = sub.symbol AND h.time = sub.first_time
        WHERE h.close IS NOT NULL
    """, (target_date, end_date)).fetchall()
    return {r["symbol"]: float(r["close"]) for r in rows}


def _query_latest_close(conn: sqlite3.Connection, year: int) -> dict[str, float]:
    rows = conn.execute("""
        SELECT symbol, close FROM stock_price_history
        WHERE time >= ? AND time < ? AND close IS NOT NULL
        GROUP BY symbol HAVING time = MAX(time)
    """, (f"{year}-01-01", f"{year + 1}-01-01")).fetchall()
    return {r["symbol"]: float(r["close"]) for r in rows}
