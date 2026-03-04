"""KTPL (Khen Thuong Phuc Loi) welfare leakage estimation.

Vietnamese companies allocate a portion of after-tax profit to employee
welfare/bonus funds before distributing to shareholders. This overstates
reported EPS relative to actual shareholder earnings.

Forensic approach:
  Leakage = (LNST_parent + Net_CF_Financing) - ΔVCSH
  Leakage_ratio = max(Leakage, 0) / LNST_parent

Limitations:
- Very noisy year-to-year (median stdev ~40pp)
- Captures ALL unexplained equity changes, not just KTPL
- Use multi-year average with heavy capping for stability

Usage:
  ratios = build_leakage_lookup(db_path, as_of_year=2018, lookback=5)
  adjusted_eps = eps * (1 - ratios.get(symbol, 0))
"""
from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from pathlib import Path

log = logging.getLogger(__name__)

MAX_RATIO = 0.50   # Cap at 50% — above this is likely measurement noise
MIN_YEARS = 3       # Need at least 3 years for a stable estimate
DEFAULT_LOOKBACK = 5  # Use 5-year rolling average


def build_leakage_lookup(
    db_path: Path,
    as_of_year: int,
    lookback: int = DEFAULT_LOOKBACK,
    max_ratio: float = MAX_RATIO,
    min_years: int = MIN_YEARS,
) -> dict[str, float]:
    """Build symbol → leakage_ratio lookup, using data available at as_of_year.

    Returns dict of {symbol: smoothed_ratio} where ratio is in [0, max_ratio].
    Only includes symbols with min_years of computable leakage data.
    No look-ahead: only uses annual data from years <= as_of_year.
    """
    start_year = as_of_year - lookback + 1

    with sqlite3.connect(str(db_path)) as conn:
        # Fetch all needed data in one query per table for efficiency
        yearly_ratios: dict[str, list[float]] = defaultdict(list)

        for yr in range(start_year, as_of_year + 1):
            rows = conn.execute("""
                SELECT
                    i.symbol,
                    i.net_profit_parent_company,
                    b_curr.equity_total,
                    b_prev.equity_total,
                    c.net_cash_from_financing_activities
                FROM income_statement i
                JOIN balance_sheet b_curr
                    ON i.symbol = b_curr.symbol
                    AND b_curr.year = ? AND b_curr.quarter IS NULL
                JOIN balance_sheet b_prev
                    ON i.symbol = b_prev.symbol
                    AND b_prev.year = ? AND b_prev.quarter IS NULL
                JOIN cash_flow_statement c
                    ON i.symbol = c.symbol
                    AND c.year = ? AND c.quarter IS NULL
                WHERE i.year = ? AND i.quarter IS NULL
                  AND i.net_profit_parent_company > 0
                  AND b_curr.equity_total IS NOT NULL
                  AND b_prev.equity_total IS NOT NULL
            """, (yr, yr - 1, yr, yr)).fetchall()

            for r in rows:
                sym = r[0]
                lnst = r[1]
                eq_curr = r[2]
                eq_prev = r[3]
                cf_fin = r[4] or 0.0

                delta_eq = eq_curr - eq_prev
                theoretical = lnst + cf_fin
                leakage = max(theoretical - delta_eq, 0)
                ratio = min(leakage / lnst, 1.0)
                yearly_ratios[sym].append(ratio)

    # Average and cap
    result = {}
    for sym, ratios in yearly_ratios.items():
        if len(ratios) < min_years:
            continue
        avg = sum(ratios) / len(ratios)
        result[sym] = min(avg, max_ratio)

    log.debug("KTPL leakage lookup: %d symbols (as_of %d, lookback %d)",
              len(result), as_of_year, lookback)
    return result
