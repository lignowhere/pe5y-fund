"""PE_TTM_20Q signal generation — select lowest P/E stocks by 20-quarter avg EPS."""
from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import AppConfig, MIN_STRATEGY_HOLDINGS
from .market_cap_filter import get_min_market_cap

log = logging.getLogger(__name__)

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
    quarter_count: int = MIN_QUARTERS,
    require_all_positive: bool = True,
    require_last_n_positive: int = 0,
    ktpl_leakage: Optional[dict[str, float]] = None,
    as_of_date: Optional[str] = None,
    financial_data_version_id: Optional[int] = None,
    signal_price_date: Optional[str] = None,
    require_official_provenance: bool = False,
) -> list[PE20QCandidate]:
    """Generate PE_TTM_20Q ranked candidate list.

    Uses up to 20 quarters of quarterly EPS instead of 5 years of annual EPS.
    Same market cap and liquidity filters as PE5Y.

    ``quarter_count`` defaults to 20 so the live strategy is unchanged. Legacy
    research backtests may request fewer quarters when the archive does not yet
    contain a full 20-quarter history.
    When require_all_positive=False, only requires avg EPS > 0 (relaxed filter).
    When require_last_n_positive > 0, last N quarters must all have EPS > 0.
    When ktpl_leakage is provided, adjusts EPS: adj_eps = eps * (1 - ratio).
    """
    if hold_year is None:
        hold_year = formation_year + 1
    if not 1 <= quarter_count <= MIN_QUARTERS:
        raise ValueError(
            f"quarter_count must be between 1 and {MIN_QUARTERS}"
        )

    sc = config.strategy
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row

        # 1) Quarterly EPS candidates
        latest_y, latest_q = _latest_quarter(hold_year, rebalance_month)
        # Mutable/live queries may clamp to what happens to be available now.
        # Snapshot backtests are strict point-in-time queries and must never
        # silently shift their historical cutoff.
        if as_of_date is None:
            latest_y, latest_q = _clamp_to_available(conn, latest_y, latest_q)
        eps_df = _query_quarterly_eps(
            conn,
            latest_y,
            latest_q,
            quarter_count=quarter_count,
            require_all_positive=require_all_positive,
            require_last_n_positive=require_last_n_positive,
            as_of_date=as_of_date,
            financial_data_version_id=financial_data_version_id,
            require_official_provenance=require_official_provenance,
        )
        if not eps_df:
            return []

        # 1b) Apply KTPL adjustment if provided
        if ktpl_leakage:
            for e in eps_df:
                ratio = ktpl_leakage.get(e["symbol"], 0.0)
                e["avg_eps"] = e["avg_eps"] * (1.0 - ratio)
            eps_df = [e for e in eps_df if e["avg_eps"] > 0]

        # 2) Price used to form the signal. It must be a completed close that
        # was knowable before execution, never the execution-session close.
        if signal_price_date:
            prices = _query_price_on_or_before(conn, signal_price_date)
        elif rebalance_date:
            prices = _query_price_on_or_after(conn, rebalance_date)
        else:
            prices = _query_latest_close(conn, formation_year)

        # 3) Market cap filter
        min_mcap = get_min_market_cap(
            formation_year, base_vnd=sc.mcap_base_vnd,
            growth_rate=sc.mcap_growth_rate,
            growth_period=sc.mcap_growth_period_years,
            base_year=sc.mcap_base_year,
        )
        if require_official_provenance:
            if not signal_price_date:
                raise ValueError(
                    "signal_price_date is required for verified market cap"
                )
            shares = _query_verified_shares(conn, signal_price_date)
            mcap = {
                symbol: prices[symbol] * CLOSE_SCALE_VND * share_count
                for symbol, share_count in shares.items()
                if symbol in prices
            }
        else:
            mcap = _query_market_cap(
                conn,
                formation_year,
                as_of_date=as_of_date,
                financial_data_version_id=financial_data_version_id,
            )
        eps_df = [e for e in eps_df if mcap.get(e["symbol"], 0) >= min_mcap]

        # 4) Liquidity filters
        liq = _query_liquidity_stats(conn, formation_year, sc)
        eps_df = [e for e in eps_df if e["symbol"] in liq]

        # 5) Compute PE and rank.
        # Annualize avg quarterly EPS (×4) so PE matches traditional convention
        candidates: list[PE20QCandidate] = []
        for e in eps_df:
            sym = e["symbol"]
            close = prices.get(sym)
            if not close or close <= 0:
                continue
            # DB stores close in thousands of VND → convert to VND
            buy_vnd = close * CLOSE_SCALE_VND
            # Sanity: price should be reasonable (1k-10M VND range)
            if buy_vnd < 1_000 or buy_vnd > 10_000_000:
                log.warning("Abnormal price for %s: %.0f VND (close_db=%.3f), skipping",
                            sym, buy_vnd, close)
                continue
            annual_eps = e["avg_eps"] * 4
            pe = buy_vnd / annual_eps
            if pe <= 0.25 or not math.isfinite(pe):
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

        # --- Anomaly monitoring ---
        n = len(candidates)
        if n == 0:
            log.warning("Signal %d: 0 candidates after all filters (year=%d, month=%d)",
                        formation_year, hold_year, rebalance_month)
        elif n < 10:
            log.warning("Signal %d: only %d candidates (unusually small universe)",
                        formation_year, n)
        else:
            log.info("Signal %d: %d candidates, PE range %.1f-%.1f",
                     formation_year, n,
                     candidates[0].pe_ttm_20q, candidates[-1].pe_ttm_20q)

        return candidates


def select_top_n_20q(
    candidates: list[PE20QCandidate],
    select_pct: float,
    min_holdings: int = MIN_STRATEGY_HOLDINGS,
) -> list[PE20QCandidate]:
    n = len(candidates)
    if n == 0:
        return []
    target = max(
        MIN_STRATEGY_HOLDINGS,
        min_holdings,
        math.ceil(n * select_pct / 100.0),
    )
    return candidates[:target]


def _latest_quarter(hold_year: int, month: int) -> tuple[int, int]:
    """Return (year, quarter) of the latest reported quarter at rebalance."""
    y_offset, q = _LATEST_Q_BY_MONTH.get(month, (0, 2))
    return hold_year + y_offset, q


def _quarter_key(year: int, quarter: int) -> int:
    return year * 10 + quarter


def _clamp_to_available(
    conn: sqlite3.Connection, target_y: int, target_q: int,
) -> tuple[int, int]:
    """Clamp (year, quarter) to the latest quarter with actual data in DB.

    If the target quarter already has data, returns it unchanged.
    Otherwise returns the most recent quarter that has EPS data.
    """
    target_key = _quarter_key(target_y, target_q)
    row = conn.execute("""
        SELECT MAX(year * 10 + quarter) as max_key
        FROM financial_ratios
        WHERE quarter IS NOT NULL AND quarter <= 4
          AND eps_vnd IS NOT NULL
          AND (year * 10 + quarter) <= ?
    """, (target_key,)).fetchone()
    if row and row["max_key"]:
        actual_key = int(row["max_key"])
        return actual_key // 10, actual_key % 10
    return target_y, target_q


# --- SQL queries (parallel to signal.py) ---

def _query_quarterly_eps(
    conn: sqlite3.Connection, end_year: int, end_quarter: int,
    *, quarter_count: int = MIN_QUARTERS,
    require_all_positive: bool = True,
    require_last_n_positive: int = 0,
    as_of_date: Optional[str] = None,
    financial_data_version_id: Optional[int] = None,
    require_official_provenance: bool = False,
) -> list[dict]:
    """A contiguous trailing window of quarterly EPS.

    ``quarter_count`` defaults to 20. A symbol must have the complete requested
    window; missing quarters are never silently averaged away.
    require_all_positive=True: every requested quarter must have EPS > 0.
    require_all_positive=False: only avg EPS > 0 required (relaxed).
    require_last_n_positive>0: last N quarters must all have EPS > 0.
    """
    if not 1 <= quarter_count <= MIN_QUARTERS:
        raise ValueError(
            f"quarter_count must be between 1 and {MIN_QUARTERS}"
        )
    end_key = _quarter_key(end_year, end_quarter)
    start_year = end_year
    start_quarter = end_quarter
    for _ in range(quarter_count - 1):
        start_quarter -= 1
        if start_quarter == 0:
            start_quarter = 4
            start_year -= 1
    start_key = _quarter_key(start_year, start_quarter)

    if require_official_provenance:
        if as_of_date is None:
            raise ValueError(
                "as_of_date is required for official point-in-time EPS"
            )
        rows = conn.execute(
            """
            WITH ranked AS (
                SELECT f.symbol, f.year, f.quarter, f.basic_eps_vnd,
                       ROW_NUMBER() OVER (
                           PARTITION BY f.symbol, f.year, f.quarter
                           ORDER BY
                                    CASE r.statement_scope
                                        WHEN 'consolidated' THEN 0 ELSE 1
                                    END,
                                    r.available_at DESC,
                                    r.revision_number DESC, r.id DESC
                       ) AS revision_rank
                FROM financial_period_facts f
                JOIN financial_filing_revisions r
                  ON r.id = f.filing_revision_id
                WHERE r.verification_status = 'verified'
                  AND f.is_independent_quarter = 1
                  AND r.available_at <= ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM financial_filing_revisions conflict
                      WHERE conflict.symbol = f.symbol
                        AND conflict.year = f.year
                        AND conflict.quarter = f.quarter
                        AND conflict.verification_status = 'conflict'
                        AND conflict.available_at <= ?
                        AND NOT EXISTS (
                            SELECT 1
                            FROM financial_filing_revisions resolution
                            WHERE resolution.supersedes_revision_id =
                                  conflict.id
                              AND resolution.verification_status = 'verified'
                              AND resolution.available_at <= ?
                        )
                  )
            )
            SELECT symbol, year, quarter, basic_eps_vnd AS eps
            FROM ranked
            WHERE revision_rank = 1
              AND (year * 10 + quarter) >= ?
              AND (year * 10 + quarter) <= ?
            ORDER BY symbol, year, quarter
            """,
            (
                as_of_date,
                as_of_date,
                as_of_date,
                start_key,
                end_key,
            ),
        ).fetchall()
    elif as_of_date is not None:
        if financial_data_version_id is None:
            raise ValueError(
                "financial_data_version_id is required for point-in-time EPS"
            )
        rows = conn.execute(
            """
            WITH ranked AS (
                SELECT symbol, year, quarter, eps_vnd,
                       ROW_NUMBER() OVER (
                           PARTITION BY symbol, year, quarter
                           ORDER BY available_at DESC,
                                    financial_data_version_id DESC, id DESC
                       ) AS revision_rank
                FROM financial_ratio_versions
                WHERE publication_status = 'verified'
                  AND available_at IS NOT NULL
                  AND available_at <= ?
                  AND financial_data_version_id <= ?
                  AND quarter IS NOT NULL AND quarter <= 4
                  AND eps_vnd IS NOT NULL
            )
            SELECT symbol, year, quarter, eps_vnd AS eps
            FROM ranked
            WHERE revision_rank = 1
              AND (year * 10 + quarter) >= ?
              AND (year * 10 + quarter) <= ?
            ORDER BY symbol, year, quarter
            """,
            (
                as_of_date,
                financial_data_version_id,
                start_key,
                end_key,
            ),
        ).fetchall()
    else:
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
        if len(eps_list) != quarter_count:
            continue
        if require_all_positive and any(e <= 0 for e in eps_list):
            continue
        if require_last_n_positive > 0:
            last_n = eps_list[-require_last_n_positive:]
            if len(last_n) < require_last_n_positive or any(e <= 0 for e in last_n):
                continue
        avg_eps = sum(eps_list) / len(eps_list)
        if avg_eps <= 0:
            continue
        result.append({"symbol": sym, "avg_eps": avg_eps, "cnt": len(eps_list)})
    return result


def _query_market_cap(
    conn: sqlite3.Connection,
    year: int,
    *,
    as_of_date: Optional[str] = None,
    financial_data_version_id: Optional[int] = None,
) -> dict[str, float]:
    if as_of_date is not None:
        if financial_data_version_id is None:
            raise ValueError(
                "financial_data_version_id is required for point-in-time market cap"
            )
        rows = conn.execute(
            """
            WITH ranked AS (
                SELECT symbol, market_cap_billions,
                       ROW_NUMBER() OVER (
                           PARTITION BY symbol, year
                           ORDER BY available_at DESC,
                                    financial_data_version_id DESC, id DESC
                       ) AS revision_rank
                FROM financial_ratio_versions
                WHERE publication_status = 'verified'
                  AND available_at IS NOT NULL
                  AND available_at <= ?
                  AND financial_data_version_id <= ?
                  AND year = ?
                  AND quarter IS NULL
                  AND market_cap_billions IS NOT NULL
            )
            SELECT symbol, market_cap_billions AS mcap
            FROM ranked WHERE revision_rank = 1
            """,
            (as_of_date, financial_data_version_id, year),
        ).fetchall()
    else:
        rows = conn.execute("""
            SELECT symbol, AVG(market_cap_billions) as mcap
            FROM financial_ratios
            WHERE year = ? AND market_cap_billions IS NOT NULL AND quarter IS NULL
            GROUP BY symbol
        """, (year,)).fetchall()
    return {r["symbol"]: float(r["mcap"]) for r in rows}


def _query_verified_shares(
    conn: sqlite3.Connection,
    signal_price_date: str,
) -> dict[str, float]:
    rows = conn.execute(
        """
        WITH ranked AS (
            SELECT symbol, shares_outstanding,
                   ROW_NUMBER() OVER (
                       PARTITION BY symbol
                       ORDER BY effective_from DESC, id DESC
                   ) AS row_rank
            FROM shares_outstanding_history
            WHERE verification_status = 'verified'
              AND effective_from <= ?
              AND (effective_to IS NULL OR effective_to >= ?)
        )
        SELECT symbol, shares_outstanding
        FROM ranked WHERE row_rank = 1
        """,
        (signal_price_date, signal_price_date),
    ).fetchall()
    return {
        row["symbol"]: float(row["shares_outstanding"])
        for row in rows
    }


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


def _query_price_on_or_before(
    conn: sqlite3.Connection,
    target_date: str,
    max_gap_days: int = 10,
) -> dict[str, float]:
    from datetime import datetime, timedelta
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    start_date = (dt - timedelta(days=max_gap_days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """
        SELECT h.symbol, h.close
        FROM stock_price_history h
        JOIN (
            SELECT symbol, MAX(time) AS last_time
            FROM stock_price_history
            WHERE time >= ? AND time <= ? AND close IS NOT NULL
            GROUP BY symbol
        ) sub ON h.symbol = sub.symbol AND h.time = sub.last_time
        WHERE h.close IS NOT NULL
        """,
        (start_date, target_date),
    ).fetchall()
    return {row["symbol"]: float(row["close"]) for row in rows}


def _query_latest_close(conn: sqlite3.Connection, year: int) -> dict[str, float]:
    rows = conn.execute("""
        SELECT h.symbol, h.close
        FROM stock_price_history h
        INNER JOIN (
            SELECT symbol, MAX(time) AS max_time
            FROM stock_price_history
            WHERE time >= ? AND time < ? AND close IS NOT NULL
            GROUP BY symbol
        ) sub ON h.symbol = sub.symbol AND h.time = sub.max_time
        WHERE h.close IS NOT NULL
    """, (f"{year}-01-01", f"{year + 1}-01-01")).fetchall()
    return {r["symbol"]: float(r["close"]) for r in rows}
