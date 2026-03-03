"""Database migrations — run once at startup, idempotent."""
from __future__ import annotations

import logging
from pathlib import Path

from ..database.connection import connect_rw, fetch_all, fetch_one

log = logging.getLogger(__name__)

# Tables that suffer from NULL-quarter UNIQUE bypass
_FINANCIAL_TABLES = [
    "balance_sheet",
    "income_statement",
    "cash_flow_statement",
    "financial_ratios",
]


def run_migrations(db_path: Path) -> None:
    """Run all pending migrations. Safe to call on every startup."""
    _fix_integer_timestamps(db_path)
    _fix_sqlite_sequence(db_path)
    _dedup_financial_tables(db_path)
    _add_financial_indexes(db_path)
    _fix_price_scale(db_path)


# ------------------------------------------------------------------
# Migration 1: integer timestamps → YYYY-MM-DD strings
# ------------------------------------------------------------------

def _fix_integer_timestamps(db_path: Path) -> int:
    """Convert integer timestamps to YYYY-MM-DD strings in price history."""
    with connect_rw(db_path) as conn:
        row = fetch_one(
            conn,
            "SELECT COUNT(*) AS n FROM stock_price_history WHERE typeof(time) = 'integer'",
            (),
        )
        count = (row or {}).get("n", 0)
        if count == 0:
            return 0

        log.info("Fixing %d integer-timestamp rows in stock_price_history", count)

        # Delete integer rows whose date already exists as text (avoid UNIQUE violation)
        conn.execute(
            """DELETE FROM stock_price_history
               WHERE typeof(time) = 'integer'
                 AND EXISTS (
                     SELECT 1 FROM stock_price_history AS t2
                     WHERE t2.symbol = stock_price_history.symbol
                       AND typeof(t2.time) = 'text'
                       AND t2.time = strftime('%Y-%m-%d', stock_price_history.time, 'unixepoch')
                 )"""
        )

        # Convert remaining integer timestamps (no conflict now)
        conn.execute(
            """UPDATE stock_price_history
               SET time = strftime('%Y-%m-%d', time, 'unixepoch')
               WHERE typeof(time) = 'integer'"""
        )

        remaining = fetch_one(
            conn,
            "SELECT COUNT(*) AS n FROM stock_price_history WHERE typeof(time) = 'integer'",
            (),
        )
        fixed = count - (remaining or {}).get("n", 0)
        log.info("Fixed %d integer-timestamp rows", fixed)
        return fixed


# ------------------------------------------------------------------
# Migration 2: fix duplicate sqlite_sequence entries
# ------------------------------------------------------------------

def _fix_sqlite_sequence(db_path: Path) -> int:
    """Remove duplicate sqlite_sequence entries, keeping highest seq per table."""
    with connect_rw(db_path) as conn:
        # Check for duplicates
        dupes = fetch_all(
            conn,
            "SELECT name, COUNT(*) AS c FROM sqlite_sequence GROUP BY name HAVING c > 1",
            (),
        )
        if not dupes:
            return 0

        log.info("Fixing %d tables with duplicate sqlite_sequence entries", len(dupes))

        # For each table with duplicates, keep only the row with highest seq
        for d in dupes:
            table_name = d["name"]
            conn.execute(
                """DELETE FROM sqlite_sequence
                   WHERE name = ? AND rowid NOT IN (
                       SELECT rowid FROM sqlite_sequence
                       WHERE name = ?
                       ORDER BY seq DESC LIMIT 1
                   )""",
                (table_name, table_name),
            )

        log.info("Fixed sqlite_sequence duplicates for: %s",
                 ", ".join(d["name"] for d in dupes))
        return len(dupes)


# ------------------------------------------------------------------
# Migration 3: deduplicate financial statement tables
# ------------------------------------------------------------------

def _dedup_financial_tables(db_path: Path) -> int:
    """Remove duplicate annual records caused by NULL quarter bypassing UNIQUE."""
    total_removed = 0
    with connect_rw(db_path) as conn:
        for table in _FINANCIAL_TABLES:
            # Check if table exists
            exists = fetch_one(
                conn,
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if not exists:
                continue

            # Count duplicates
            row = fetch_one(
                conn,
                f"""SELECT COUNT(*) AS n FROM {table}
                    WHERE id NOT IN (
                        SELECT MAX(id) FROM {table}
                        GROUP BY symbol, period, year, COALESCE(quarter, -1)
                    )""",
                (),
            )
            dup_count = (row or {}).get("n", 0)
            if dup_count == 0:
                continue

            log.info("Removing %d duplicate rows from %s", dup_count, table)

            conn.execute(
                f"""DELETE FROM {table}
                    WHERE id NOT IN (
                        SELECT MAX(id) FROM {table}
                        GROUP BY symbol, period, year, COALESCE(quarter, -1)
                    )"""
            )
            total_removed += dup_count

            # Add partial unique indexes to prevent recurrence
            conn.execute(
                f"""CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_annual
                    ON {table}(symbol, period, year) WHERE quarter IS NULL"""
            )
            conn.execute(
                f"""CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_quarterly
                    ON {table}(symbol, period, year, quarter)
                    WHERE quarter IS NOT NULL"""
            )

    if total_removed > 0:
        log.info("Total duplicate rows removed: %d", total_removed)
    return total_removed


# ------------------------------------------------------------------
# Migration 4: add composite indexes for query performance
# ------------------------------------------------------------------

def _add_financial_indexes(db_path: Path) -> None:
    """Add composite indexes on (symbol, year, quarter) for financial queries."""
    with connect_rw(db_path) as conn:
        for table in _FINANCIAL_TABLES:
            exists = fetch_one(
                conn,
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if not exists:
                continue
            conn.execute(
                f"""CREATE INDEX IF NOT EXISTS idx_{table}_sym_yr_qtr
                    ON {table}(symbol, year, quarter)"""
            )


# ------------------------------------------------------------------
# Migration 5: fix VCI prices stored in VND instead of thousands
# ------------------------------------------------------------------

def _fix_price_scale(db_path: Path) -> int:
    """Fix prices accidentally stored in VND instead of thousands.

    VCI API returns prices in VND (e.g. 66600 for VNM).
    DB convention: store in thousands (e.g. 66.6 for VNM).
    The updater previously inserted raw VCI values → ~1000x too high.

    Safe heuristic: the most expensive VN stock is ~250k VND = 250 in
    thousands.  Any close > 500 is guaranteed to be in the wrong scale.
    """
    with connect_rw(db_path) as conn:
        row = fetch_one(
            conn,
            "SELECT COUNT(*) AS n FROM stock_price_history WHERE close > 500",
            (),
        )
        count = (row or {}).get("n", 0)
        if count == 0:
            return 0

        log.info("Fixing %d price rows stored in VND instead of thousands", count)
        conn.execute(
            """UPDATE stock_price_history
               SET open  = open  / 1000.0,
                   high  = high  / 1000.0,
                   low   = low   / 1000.0,
                   close = close / 1000.0
               WHERE close > 500"""
        )
        log.info("Fixed %d price rows to thousands scale", count)
        return count
