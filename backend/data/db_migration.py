"""Database migrations — run once at startup, idempotent."""
from __future__ import annotations

import json
import hashlib
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
    _create_fund_tables(db_path)
    _create_snapshot_tables(db_path)
    _create_adjusted_price_cache(db_path)
    _create_hardening_tables(db_path)
    _create_trusted_local_tables(db_path)
    _create_financial_safety_tables(db_path)
    _create_legacy_reuse_tables(db_path)
    _mark_partial_latest_session_provisional(db_path)
    _repair_legacy_foreign_keys(db_path)
    _migrate_saved_portfolio(db_path)


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


# ------------------------------------------------------------------
# Migration 6: durable fund preferences, holdings, and sync history
# ------------------------------------------------------------------

def _create_fund_tables(db_path: Path) -> None:
    """Create the small application-owned tables used by the fund planner."""
    with connect_rw(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS fund_preferences (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                strategy TEXT NOT NULL,
                select_pct REAL NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS fund_holdings (
                symbol TEXT PRIMARY KEY,
                shares INTEGER NOT NULL CHECK (shares >= 0),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS data_sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'starting',
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT,
                prices_updated INTEGER NOT NULL DEFAULT 0,
                prices_failed INTEGER NOT NULL DEFAULT 0,
                financials_updated INTEGER NOT NULL DEFAULT 0,
                financials_failed INTEGER NOT NULL DEFAULT 0,
                message TEXT
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_data_sync_runs_started
               ON data_sync_runs(started_at DESC)"""
        )
        _ensure_column(
            conn, "data_sync_runs", "financial_symbols_total",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            conn, "data_sync_runs", "price_symbols_total",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            conn, "data_sync_runs", "prices_processed",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            conn, "data_sync_runs", "financial_rows_staged",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(conn, "data_sync_runs", "financial_version_id", "INTEGER")
        _ensure_column(conn, "data_sync_runs", "snapshot_set_id", "INTEGER")
        _ensure_column(
            conn,
            "data_sync_runs",
            "cancel_requested",
            "INTEGER NOT NULL DEFAULT 0",
        )


def _ensure_column(conn, table: str, column: str, declaration: str) -> None:
    existing = {
        row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
    }
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


# ------------------------------------------------------------------
# Migration 7: atomic financial versions and immutable strategy snapshots
# ------------------------------------------------------------------

def _create_snapshot_tables(db_path: Path) -> None:
    """Create versioned financial-sync and strategy-cycle snapshot tables."""
    with connect_rw(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS financial_data_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_api TEXT NOT NULL,
                as_of_year INTEGER NOT NULL,
                as_of_quarter INTEGER NOT NULL,
                content_hash TEXT NOT NULL UNIQUE,
                row_count INTEGER NOT NULL,
                symbol_count INTEGER NOT NULL,
                sync_run_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1))
            )"""
        )
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_financial_version_active
               ON financial_data_versions(is_active) WHERE is_active = 1"""
        )
        _ensure_column(
            conn,
            "financial_data_versions",
            "point_in_time_ready",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            conn,
            "financial_data_versions",
            "publication_coverage_pct",
            "REAL NOT NULL DEFAULT 0",
        )
        _ensure_column(
            conn,
            "financial_data_versions",
            "verified_row_count",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            conn,
            "financial_data_versions",
            "methodology_version",
            "TEXT NOT NULL DEFAULT 'legacy_mutable'",
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS financial_ratios_staging (
                run_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                period TEXT NOT NULL,
                year INTEGER NOT NULL,
                quarter INTEGER,
                price_to_book REAL,
                price_to_earnings REAL,
                eps_vnd REAL,
                bvps_vnd REAL,
                roe REAL,
                market_cap_billions REAL,
                shares_outstanding_millions REAL,
                data_json TEXT,
                source TEXT NOT NULL,
                PRIMARY KEY (run_id, symbol, period)
            )"""
        )
        for column, declaration in (
            ("public_date", "TEXT"),
            ("source_created_at", "TEXT"),
            ("source_updated_at", "TEXT"),
            ("available_at", "TEXT"),
            (
                "publication_status",
                "TEXT NOT NULL DEFAULT 'legacy_unverified'",
            ),
        ):
            _ensure_column(
                conn, "financial_ratios_staging", column, declaration
            )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_financial_stage_run
               ON financial_ratios_staging(run_id, symbol, year, quarter)"""
        )
        if fetch_one(
            conn,
            """SELECT name FROM sqlite_master
               WHERE type = 'table' AND name = 'financial_ratios'""",
        ):
            for column, declaration in (
                ("public_date", "TEXT"),
                ("source_created_at", "TEXT"),
                ("source_updated_at", "TEXT"),
                ("available_at", "TEXT"),
                (
                    "publication_status",
                    "TEXT NOT NULL DEFAULT 'legacy_unverified'",
                ),
                ("financial_data_version_id", "INTEGER"),
            ):
                _ensure_column(
                    conn, "financial_ratios", column, declaration
                )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS financial_ratio_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                financial_data_version_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                period TEXT NOT NULL,
                year INTEGER NOT NULL,
                quarter INTEGER,
                price_to_book REAL,
                price_to_earnings REAL,
                eps_vnd REAL,
                bvps_vnd REAL,
                roe REAL,
                market_cap_billions REAL,
                shares_outstanding_millions REAL,
                data_json TEXT,
                source TEXT NOT NULL,
                public_date TEXT,
                source_created_at TEXT,
                source_updated_at TEXT,
                available_at TEXT,
                publication_status TEXT NOT NULL,
                observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(financial_data_version_id)
                    REFERENCES financial_data_versions(id),
                UNIQUE(financial_data_version_id, symbol, period)
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_financial_ratio_pit
               ON financial_ratio_versions(
                   symbol, year, quarter, available_at,
                   financial_data_version_id
               )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_financial_ratio_version
               ON financial_ratio_versions(financial_data_version_id)"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS immutable_financial_ratio_version_update
               BEFORE UPDATE ON financial_ratio_versions
               BEGIN
                 SELECT RAISE(ABORT, 'financial ratio versions are immutable');
               END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS immutable_financial_ratio_version_delete
               BEFORE DELETE ON financial_ratio_versions
               BEGIN
                 SELECT RAISE(ABORT, 'financial ratio versions are immutable');
               END"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS financial_sync_symbols (
                run_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL,
                row_count INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (run_id, symbol)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS strategy_snapshot_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                financial_data_version_id INTEGER NOT NULL,
                config_json TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                backtest_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                activated_at TEXT,
                is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
                FOREIGN KEY(financial_data_version_id)
                    REFERENCES financial_data_versions(id)
            )"""
        )
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_strategy_snapshot_set_active
               ON strategy_snapshot_sets(is_active) WHERE is_active = 1"""
        )
        _ensure_column(
            conn,
            "strategy_snapshot_sets",
            "methodology_version",
            "TEXT NOT NULL DEFAULT 'legacy_mutable'",
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS strategy_cycle_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_set_id INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                select_pct REAL NOT NULL,
                formation_year INTEGER NOT NULL,
                hold_year INTEGER NOT NULL,
                rebalance_date TEXT NOT NULL,
                universe_count INTEGER NOT NULL,
                selected_count INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(snapshot_set_id)
                    REFERENCES strategy_snapshot_sets(id),
                UNIQUE(snapshot_set_id, strategy, select_pct, hold_year)
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_strategy_cycle_lookup
               ON strategy_cycle_snapshots(
                   strategy, select_pct, hold_year, snapshot_set_id
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS strategy_cycle_snapshot_items (
                cycle_snapshot_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                signal_rank INTEGER NOT NULL,
                avg_eps_20q REAL NOT NULL,
                pe_ttm_20q REAL NOT NULL,
                market_cap_vnd REAL NOT NULL,
                quarters_count INTEGER NOT NULL,
                rebalance_price_vnd REAL NOT NULL,
                rebalance_price_date TEXT NOT NULL,
                adv_20d_shares REAL NOT NULL,
                initial_weight REAL NOT NULL,
                PRIMARY KEY(cycle_snapshot_id, symbol),
                UNIQUE(cycle_snapshot_id, signal_rank),
                FOREIGN KEY(cycle_snapshot_id)
                    REFERENCES strategy_cycle_snapshots(id)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS strategy_backtest_results (
                snapshot_set_id INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                select_pct REAL NOT NULL,
                start_hold_year INTEGER NOT NULL,
                end_hold_year INTEGER NOT NULL,
                capital_vnd REAL NOT NULL,
                net_cagr REAL NOT NULL,
                win_rate REAL NOT NULL,
                yearly_json TEXT NOT NULL,
                PRIMARY KEY(snapshot_set_id, strategy, select_pct),
                FOREIGN KEY(snapshot_set_id)
                    REFERENCES strategy_snapshot_sets(id)
            )"""
        )

        # Cycle rows and their members are append-only. New revisions are
        # represented by a new snapshot set and activated atomically.
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS immutable_strategy_cycle_update
               BEFORE UPDATE ON strategy_cycle_snapshots
               BEGIN
                 SELECT RAISE(ABORT, 'strategy cycle snapshots are immutable');
               END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS immutable_strategy_set_payload
               BEFORE UPDATE OF financial_data_version_id, config_json,
                                config_hash, backtest_json, created_at
               ON strategy_snapshot_sets
               BEGIN
                 SELECT RAISE(ABORT, 'strategy snapshot payloads are immutable');
               END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS immutable_strategy_set_delete
               BEFORE DELETE ON strategy_snapshot_sets
               BEGIN
                 SELECT RAISE(ABORT, 'strategy snapshot sets are immutable');
               END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS immutable_strategy_cycle_delete
               BEFORE DELETE ON strategy_cycle_snapshots
               BEGIN
                 SELECT RAISE(ABORT, 'strategy cycle snapshots are immutable');
               END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS immutable_strategy_item_update
               BEFORE UPDATE ON strategy_cycle_snapshot_items
               BEGIN
                 SELECT RAISE(ABORT, 'strategy cycle snapshot items are immutable');
               END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS immutable_strategy_item_delete
               BEFORE DELETE ON strategy_cycle_snapshot_items
               BEGIN
                 SELECT RAISE(ABORT, 'strategy cycle snapshot items are immutable');
               END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS immutable_active_cycle_insert
               BEFORE INSERT ON strategy_cycle_snapshots
               WHEN EXISTS (
                 SELECT 1 FROM strategy_snapshot_sets s
                 WHERE s.id = NEW.snapshot_set_id AND s.is_active = 1
               )
               BEGIN
                 SELECT RAISE(ABORT, 'active strategy snapshot sets are immutable');
               END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS immutable_active_item_insert
               BEFORE INSERT ON strategy_cycle_snapshot_items
               WHEN EXISTS (
                 SELECT 1
                 FROM strategy_cycle_snapshots c
                 JOIN strategy_snapshot_sets s
                   ON s.id = c.snapshot_set_id
                 WHERE c.id = NEW.cycle_snapshot_id AND s.is_active = 1
               )
               BEGIN
                 SELECT RAISE(ABORT, 'active strategy snapshot sets are immutable');
               END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS immutable_backtest_update
               BEFORE UPDATE ON strategy_backtest_results
               BEGIN
                 SELECT RAISE(ABORT, 'strategy backtest results are immutable');
               END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS immutable_backtest_delete
               BEFORE DELETE ON strategy_backtest_results
               BEGIN
                 SELECT RAISE(ABORT, 'strategy backtest results are immutable');
               END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS immutable_active_backtest_insert
               BEFORE INSERT ON strategy_backtest_results
               WHEN EXISTS (
                 SELECT 1 FROM strategy_snapshot_sets s
                 WHERE s.id = NEW.snapshot_set_id AND s.is_active = 1
               )
               BEGIN
                 SELECT RAISE(ABORT, 'active strategy snapshot sets are immutable');
               END"""
        )
    # Direct callers (including isolated snapshot tests) also need the v2
    # metadata tables.  The helper is idempotent and is defined below.
    _create_hardening_tables(db_path)
    _create_trusted_local_tables(db_path)
    _create_financial_safety_tables(db_path)


def _create_adjusted_price_cache(db_path: Path) -> None:
    """Cache mutable adjusted-price inputs outside immutable snapshots."""
    with connect_rw(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS strategy_adjusted_price_cache (
                cycle_snapshot_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                rebalance_price_date TEXT NOT NULL,
                adjusted_rebalance_price_vnd REAL NOT NULL,
                valuation_date TEXT NOT NULL,
                adjusted_current_price_vnd REAL NOT NULL,
                source TEXT NOT NULL,
                fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (cycle_snapshot_id, symbol)
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_adjusted_price_cache_valuation
               ON strategy_adjusted_price_cache(
                   cycle_snapshot_id, valuation_date
               )"""
        )


def _create_hardening_tables(db_path: Path) -> None:
    """Add provenance, research, configuration and operations metadata.

    These tables are additive.  They deliberately do not rewrite the large
    legacy price or financial tables during application startup.
    """
    with connect_rw(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS market_price_metadata (
                symbol TEXT NOT NULL,
                price_date TEXT NOT NULL,
                source TEXT NOT NULL,
                price_basis TEXT NOT NULL,
                raw_unit TEXT NOT NULL,
                is_provisional INTEGER NOT NULL DEFAULT 0
                    CHECK (is_provisional IN (0, 1)),
                observed_at TEXT NOT NULL,
                PRIMARY KEY (symbol, price_date)
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_price_metadata_session
               ON market_price_metadata(
                   price_date, is_provisional, source, price_basis
               )"""
        )
        _ensure_column(
            conn, "market_price_metadata", "source_url", "TEXT"
        )
        _ensure_column(
            conn,
            "market_price_metadata",
            "source_payload_sha256",
            "TEXT",
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS adjusted_price_history (
                symbol TEXT NOT NULL,
                price_date TEXT NOT NULL,
                close_vnd REAL NOT NULL CHECK (close_vnd > 0),
                source TEXT NOT NULL,
                price_basis TEXT NOT NULL
                    CHECK (price_basis = 'adjusted_total_return'),
                source_as_of TEXT NOT NULL,
                fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, price_date, source_as_of)
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_adjusted_history_lookup
               ON adjusted_price_history(symbol, price_date, source_as_of)"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS data_source_health (
                source TEXT NOT NULL,
                capability TEXT NOT NULL,
                available INTEGER NOT NULL CHECK (available IN (0, 1)),
                last_status_code INTEGER,
                last_error TEXT,
                checked_at TEXT NOT NULL,
                PRIMARY KEY (source, capability)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS data_health_summary (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                summary_json TEXT NOT NULL,
                refreshed_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS strategy_config_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                config_json TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN ('pending', 'active', 'failed')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                activated_at TEXT,
                error TEXT
            )"""
        )
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_strategy_config_active
               ON strategy_config_versions(status) WHERE status = 'active'"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_strategy_config_pending
               ON strategy_config_versions(status, id DESC)"""
        )
        active_config = fetch_one(
            conn,
            """SELECT id FROM strategy_config_versions
               WHERE status = 'active' LIMIT 1""",
        )
        if not active_config:
            overrides: dict = {}
            legacy_config = db_path.parent / "strategy_config.json"
            if legacy_config.exists():
                try:
                    loaded = json.loads(
                        legacy_config.read_text(encoding="utf-8")
                    )
                    if isinstance(loaded, dict):
                        overrides = loaded
                except (OSError, ValueError):
                    log.warning(
                        "Could not import legacy strategy_config.json"
                    )
            payload = json.dumps(
                overrides,
                sort_keys=True,
                separators=(",", ":"),
            )
            conn.execute(
                """INSERT INTO strategy_config_versions
                   (config_json, config_hash, status, activated_at)
                   VALUES (?, ?, 'active', CURRENT_TIMESTAMP)""",
                (
                    payload,
                    hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                ),
            )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS strategy_research_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_set_id INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                select_pct REAL NOT NULL,
                formation_year INTEGER NOT NULL,
                hold_year INTEGER NOT NULL,
                rebalance_date TEXT NOT NULL,
                quarter_count INTEGER NOT NULL,
                universe_count INTEGER NOT NULL,
                selected_count INTEGER NOT NULL,
                pit_tier TEXT NOT NULL DEFAULT 'legacy_research',
                price_basis TEXT NOT NULL,
                data_checksum TEXT NOT NULL,
                excluded_reason TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(snapshot_set_id)
                    REFERENCES strategy_snapshot_sets(id),
                UNIQUE(snapshot_set_id, strategy, select_pct, hold_year)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS strategy_research_cycle_items (
                research_cycle_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                signal_rank INTEGER NOT NULL,
                avg_eps REAL NOT NULL,
                pe REAL NOT NULL,
                market_cap_vnd REAL NOT NULL,
                quarters_count INTEGER NOT NULL,
                rebalance_price_vnd REAL NOT NULL,
                rebalance_price_date TEXT NOT NULL,
                adv_20d_shares REAL NOT NULL,
                initial_weight REAL NOT NULL,
                PRIMARY KEY(research_cycle_id, symbol),
                UNIQUE(research_cycle_id, signal_rank),
                FOREIGN KEY(research_cycle_id)
                    REFERENCES strategy_research_cycles(id)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS strategy_backtest_results_v2 (
                snapshot_set_id INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                select_pct REAL NOT NULL,
                pit_tier TEXT NOT NULL,
                start_hold_year INTEGER NOT NULL,
                end_hold_year INTEGER NOT NULL,
                cycle_count INTEGER NOT NULL,
                capital_vnd REAL NOT NULL,
                net_cagr REAL NOT NULL,
                win_rate REAL NOT NULL,
                price_basis TEXT NOT NULL,
                benchmark_symbol TEXT NOT NULL,
                benchmark_cagr REAL,
                yearly_json TEXT NOT NULL,
                excluded_cycles_json TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY(
                    snapshot_set_id, strategy, select_pct, pit_tier
                ),
                FOREIGN KEY(snapshot_set_id)
                    REFERENCES strategy_snapshot_sets(id)
            )"""
        )
        for column, declaration in (
            ("quarter_count", "INTEGER NOT NULL DEFAULT 20"),
            ("pit_tier", "TEXT NOT NULL DEFAULT 'strict_pit'"),
            ("price_basis", "TEXT NOT NULL DEFAULT 'unverified_raw_price'"),
            ("data_checksum", "TEXT"),
            ("excluded_reason", "TEXT"),
        ):
            _ensure_column(
                conn, "strategy_cycle_snapshots", column, declaration
            )
        for column, declaration in (
            ("price_basis", "TEXT NOT NULL DEFAULT 'unverified_raw_price'"),
            ("pit_policy", "TEXT NOT NULL DEFAULT 'two_tier_v1'"),
            (
                "execution_price_basis",
                "TEXT NOT NULL DEFAULT 'legacy_unknown'",
            ),
            (
                "signal_price_basis",
                "TEXT NOT NULL DEFAULT 'legacy_unknown'",
            ),
        ):
            _ensure_column(
                conn, "strategy_snapshot_sets", column, declaration
            )
        for column, declaration in (
            ("official_provenance_ready", "INTEGER NOT NULL DEFAULT 0"),
            (
                "quality_status",
                "TEXT NOT NULL DEFAULT 'vendor_research'",
            ),
            ("quality_issues_json", "TEXT NOT NULL DEFAULT '[]'"),
        ):
            _ensure_column(
                conn, "financial_data_versions", column, declaration
            )
        for table in (
            "strategy_research_cycles",
            "strategy_research_cycle_items",
            "strategy_backtest_results_v2",
        ):
            conn.execute(
                f"""CREATE TRIGGER IF NOT EXISTS immutable_{table}_update
                    BEFORE UPDATE ON {table}
                    BEGIN
                      SELECT RAISE(
                        ABORT, 'strategy research snapshots are immutable'
                      );
                    END"""
            )
            conn.execute(
                f"""CREATE TRIGGER IF NOT EXISTS immutable_{table}_delete
                    BEFORE DELETE ON {table}
                    BEGIN
                      SELECT RAISE(
                        ABORT, 'strategy research snapshots are immutable'
                      );
                    END"""
            )


def _create_financial_safety_tables(db_path: Path) -> None:
    """Create append-only financial provenance and investment safety gates."""
    with connect_rw(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS financial_filing_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                year INTEGER NOT NULL,
                quarter INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
                statement_scope TEXT NOT NULL
                    CHECK (statement_scope IN ('consolidated', 'standalone')),
                revision_number INTEGER NOT NULL DEFAULT 1,
                basic_eps_vnd REAL NOT NULL,
                published_at TEXT,
                first_observed_at TEXT NOT NULL,
                available_at TEXT NOT NULL,
                availability_basis TEXT NOT NULL
                    CHECK (availability_basis IN (
                        'official_timestamp',
                        'official_date_next_session',
                        'live_observed'
                    )),
                source_authority TEXT NOT NULL,
                source_url TEXT,
                document_sha256 TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                verification_status TEXT NOT NULL
                    CHECK (verification_status IN (
                        'verified', 'conflict', 'ingestion_missing',
                        'source_empty', 'not_published', 'not_applicable'
                    )),
                supersedes_revision_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(supersedes_revision_id)
                    REFERENCES financial_filing_revisions(id),
                UNIQUE(symbol, year, quarter, statement_scope, content_sha256)
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_filing_revision_pit
               ON financial_filing_revisions(
                   symbol, year, quarter, available_at,
                   verification_status, revision_number
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS financial_period_facts (
                filing_revision_id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                year INTEGER NOT NULL,
                quarter INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
                basic_eps_vnd REAL NOT NULL,
                is_independent_quarter INTEGER NOT NULL DEFAULT 1
                    CHECK (is_independent_quarter IN (0, 1)),
                extracted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                extractor_version TEXT NOT NULL,
                FOREIGN KEY(filing_revision_id)
                    REFERENCES financial_filing_revisions(id)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS shares_outstanding_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                effective_from TEXT NOT NULL,
                effective_to TEXT,
                shares_outstanding REAL NOT NULL
                    CHECK (shares_outstanding > 0),
                source_authority TEXT NOT NULL,
                source_url TEXT,
                document_sha256 TEXT NOT NULL,
                verification_status TEXT NOT NULL
                    CHECK (verification_status IN ('verified', 'conflict')),
                observed_at TEXT NOT NULL,
                UNIQUE(
                    symbol, effective_from, shares_outstanding,
                    document_sha256
                )
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_shares_history_pit
               ON shares_outstanding_history(
                   symbol, effective_from, effective_to,
                   verification_status
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS corporate_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                action_type TEXT NOT NULL
                    CHECK (action_type IN (
                        'cash_dividend', 'stock_dividend', 'split',
                        'rights_issue', 'other'
                    )),
                ex_date TEXT NOT NULL,
                record_date TEXT,
                payment_date TEXT,
                cash_vnd_per_share REAL,
                share_factor REAL,
                subscription_price_vnd REAL,
                source_authority TEXT NOT NULL,
                source_url TEXT,
                document_sha256 TEXT NOT NULL,
                verification_status TEXT NOT NULL
                    CHECK (verification_status IN (
                        'verified', 'conflict', 'unsupported'
                    )),
                observed_at TEXT NOT NULL,
                UNIQUE(symbol, action_type, ex_date, document_sha256)
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_corporate_actions_period
               ON corporate_actions(symbol, ex_date, verification_status)"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS corporate_action_coverage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                coverage_status TEXT NOT NULL
                    CHECK (coverage_status IN ('verified', 'conflict')),
                source_authority TEXT NOT NULL,
                document_sha256 TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                UNIQUE(
                    symbol, start_date, end_date,
                    source_authority, document_sha256
                )
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS price_source_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                price_date TEXT NOT NULL,
                open_vnd REAL NOT NULL CHECK (open_vnd > 0),
                high_vnd REAL NOT NULL CHECK (high_vnd > 0),
                low_vnd REAL NOT NULL CHECK (low_vnd > 0),
                close_vnd REAL NOT NULL CHECK (close_vnd > 0),
                volume REAL NOT NULL CHECK (volume >= 0),
                source TEXT NOT NULL,
                source_url TEXT,
                payload_sha256 TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                is_session_final INTEGER NOT NULL
                    CHECK (is_session_final IN (0, 1)),
                verification_status TEXT NOT NULL
                    CHECK (verification_status IN ('verified', 'conflict')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, price_date, source, payload_sha256)
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_price_observation_lookup
               ON price_source_observations(
                   symbol, price_date, source, verification_status
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS benchmark_total_return_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                price_date TEXT NOT NULL,
                index_value REAL NOT NULL CHECK (index_value > 0),
                source_authority TEXT NOT NULL,
                source_url TEXT,
                document_sha256 TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                verification_status TEXT NOT NULL
                    CHECK (verification_status IN ('verified', 'conflict')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, price_date, source_authority, document_sha256)
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_benchmark_total_return_lookup
               ON benchmark_total_return_history(
                   symbol, price_date, verification_status
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS official_provenance_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manifest_sha256 TEXT NOT NULL UNIQUE,
                as_of_year INTEGER NOT NULL,
                as_of_quarter INTEGER NOT NULL
                    CHECK (as_of_quarter BETWEEN 1 AND 4),
                classification_cutoff TEXT NOT NULL,
                source_authority TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS official_symbol_classifications (
                batch_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN (
                        'verified', 'not_published', 'not_applicable',
                        'source_empty', 'ingestion_missing', 'conflict'
                    )),
                source_authority TEXT NOT NULL,
                source_url TEXT,
                document_sha256 TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                reason TEXT,
                PRIMARY KEY(batch_id, symbol),
                FOREIGN KEY(batch_id)
                    REFERENCES official_provenance_batches(id)
            )"""
        )
        _ensure_column(
            conn,
            "financial_data_versions",
            "provenance_batch_id",
            "INTEGER",
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS financial_sync_symbol_history (
                run_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN (
                        'verified', 'not_published', 'not_applicable',
                        'source_empty', 'ingestion_missing', 'conflict',
                        'error'
                    )),
                row_count INTEGER NOT NULL DEFAULT 0,
                required_for_investment INTEGER NOT NULL DEFAULT 0
                    CHECK (required_for_investment IN (0, 1)),
                error TEXT,
                observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (run_id, symbol)
            )"""
        )
        for column, declaration in (
            (
                "lifecycle_status",
                "TEXT NOT NULL DEFAULT 'quarantined'",
            ),
            ("portfolio_ready", "INTEGER NOT NULL DEFAULT 0"),
            ("performance_ready", "INTEGER NOT NULL DEFAULT 0"),
            ("backtest_ready", "INTEGER NOT NULL DEFAULT 0"),
            ("blocking_issues_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("validation_report_hash", "TEXT"),
            ("validated_at", "TEXT"),
            ("trusted_local_ready", "INTEGER NOT NULL DEFAULT 0"),
            ("trusted_local_attestation_id", "INTEGER"),
        ):
            _ensure_column(
                conn, "strategy_snapshot_sets", column, declaration
            )
        for column, declaration in (
            ("signal_cutoff", "TEXT"),
            ("signal_price_date", "TEXT"),
            ("execution_date", "TEXT"),
        ):
            _ensure_column(
                conn, "strategy_cycle_snapshots", column, declaration
            )
        for column, declaration in (
            ("signal_price_vnd", "REAL"),
            ("execution_price_vnd", "REAL"),
            ("execution_price_date", "TEXT"),
            ("shares_outstanding", "REAL"),
            ("filing_revision_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("price_provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("provenance_checksum", "TEXT"),
        ):
            _ensure_column(
                conn, "strategy_cycle_snapshot_items", column, declaration
            )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_snapshot_investment_ready
               ON strategy_snapshot_sets(
                   is_active, lifecycle_status, portfolio_ready,
                   performance_ready, backtest_ready
               )"""
        )
        # Every pre-safety snapshot lacks official filing and execution
        # provenance. Keep it visible for research, but never investment use.
        conn.execute(
            """UPDATE strategy_snapshot_sets
               SET lifecycle_status = 'quarantined',
                   portfolio_ready = 0,
                   performance_ready = 0,
                   backtest_ready = 0,
                   blocking_issues_json = CASE
                       WHEN blocking_issues_json = '[]' THEN
                         '["LEGACY_SNAPSHOT_UNVERIFIED"]'
                       ELSE blocking_issues_json
                   END
               WHERE (
                       COALESCE(
                           execution_price_basis, 'legacy_unknown'
                       ) <> 'verified_execution_unadjusted'
                    OR COALESCE(
                           signal_price_basis, 'legacy_unknown'
                       ) <> 'verified_signal_unadjusted'
                   )
                 AND COALESCE(trusted_local_ready, 0) = 0"""
        )
        for table in (
            "financial_filing_revisions",
            "financial_period_facts",
            "shares_outstanding_history",
            "corporate_actions",
            "corporate_action_coverage",
            "price_source_observations",
            "benchmark_total_return_history",
            "official_provenance_batches",
            "official_symbol_classifications",
        ):
            conn.execute(
                f"""CREATE TRIGGER IF NOT EXISTS immutable_{table}_update
                    BEFORE UPDATE ON {table}
                    BEGIN
                      SELECT RAISE(
                        ABORT, 'financial provenance records are immutable'
                      );
                    END"""
            )
            conn.execute(
                f"""CREATE TRIGGER IF NOT EXISTS immutable_{table}_delete
                    BEFORE DELETE ON {table}
                    BEGIN
                      SELECT RAISE(
                        ABORT, 'financial provenance records are immutable'
                      );
                    END"""
            )
        # Vendor publication dates remain valuable research metadata, but
        # they are not documentary strict-PIT evidence.
        conn.execute(
            """UPDATE financial_data_versions
               SET point_in_time_ready = 0
               WHERE COALESCE(official_provenance_ready, 0) = 0"""
        )


def _create_trusted_local_tables(db_path: Path) -> None:
    """Persist an explicit owner decision to use the existing local data.

    This is intentionally separate from official-document provenance.  A
    trusted-local attestation unlocks a clearly labelled local-data snapshot;
    it never changes strict-PIT or official verification flags.
    """
    with connect_rw(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS trusted_local_attestations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                financial_data_version_id INTEGER NOT NULL,
                financial_content_hash TEXT NOT NULL,
                source_database_sha256 TEXT NOT NULL,
                source_backup_path TEXT NOT NULL,
                source_backup_sha256 TEXT NOT NULL,
                statement TEXT NOT NULL,
                attested_by TEXT NOT NULL DEFAULT 'fund_owner',
                attested_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 0
                    CHECK (is_active IN (0, 1)),
                revoked_at TEXT,
                attestation_hash TEXT NOT NULL UNIQUE,
                FOREIGN KEY(financial_data_version_id)
                    REFERENCES financial_data_versions(id)
            )"""
        )
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS
                   idx_one_active_trusted_local_attestation
               ON trusted_local_attestations(is_active)
               WHERE is_active = 1"""
        )
        _ensure_column(
            conn,
            "strategy_snapshot_sets",
            "trusted_local_ready",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            conn,
            "strategy_snapshot_sets",
            "trusted_local_attestation_id",
            "INTEGER",
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_snapshot_trusted_local
               ON strategy_snapshot_sets(
                   is_active, trusted_local_ready
               )"""
        )


def _create_legacy_reuse_tables(db_path: Path) -> None:
    """Create a non-authoritative inventory for reusing the existing DB.

    These tables deliberately sit outside the official provenance ledger.
    A legacy row may reduce the amount of data that must be downloaded, but
    it can never satisfy an investment-readiness gate by itself.
    """
    with connect_rw(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS legacy_reuse_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_fingerprint TEXT NOT NULL,
                start_year INTEGER NOT NULL,
                end_year INTEGER NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN ('running', 'completed', 'failed')),
                universe_count INTEGER NOT NULL DEFAULT 0,
                stats_json TEXT NOT NULL DEFAULT '{}',
                error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT,
                UNIQUE(source_fingerprint, start_year, end_year)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS legacy_symbol_inventory (
                run_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                exchange TEXT,
                listed_date TEXT,
                first_price_date TEXT,
                last_price_date TEXT,
                price_rows INTEGER NOT NULL DEFAULT 0,
                quarterly_rows INTEGER NOT NULL DEFAULT 0,
                vendor_dated_rows INTEGER NOT NULL DEFAULT 0,
                missing_quarter_count INTEGER NOT NULL DEFAULT 0,
                event_rows INTEGER NOT NULL DEFAULT 0,
                adjusted_price_rows INTEGER NOT NULL DEFAULT 0,
                baseline_status TEXT NOT NULL,
                baseline_sha256 TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (run_id, symbol),
                FOREIGN KEY(run_id) REFERENCES legacy_reuse_runs(id)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS legacy_cycle_inventory (
                run_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                cycle_year INTEGER NOT NULL,
                signal_date TEXT NOT NULL,
                execution_date TEXT NOT NULL,
                signal_close_legacy REAL,
                execution_open_legacy REAL,
                adv_sessions INTEGER NOT NULL DEFAULT 0,
                baseline_status TEXT NOT NULL,
                baseline_sha256 TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (run_id, symbol, cycle_year),
                FOREIGN KEY(run_id) REFERENCES legacy_reuse_runs(id)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS legacy_verification_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                evidence_type TEXT NOT NULL
                    CHECK (evidence_type IN (
                        'financial_filing', 'market_price',
                        'shares_outstanding', 'corporate_action',
                        'benchmark_total_return'
                    )),
                period_key TEXT NOT NULL DEFAULT '',
                priority INTEGER NOT NULL,
                baseline_status TEXT NOT NULL,
                verification_status TEXT NOT NULL DEFAULT
                    'needs_official_evidence'
                    CHECK (verification_status IN (
                        'needs_official_evidence', 'in_review',
                        'verified_imported', 'not_applicable',
                        'conflict', 'blocked'
                    )),
                baseline_sha256 TEXT NOT NULL,
                missing_fields_json TEXT NOT NULL DEFAULT '[]',
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(run_id, symbol, evidence_type, period_key),
                FOREIGN KEY(run_id) REFERENCES legacy_reuse_runs(id)
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_legacy_queue_work
               ON legacy_verification_queue(
                   verification_status, priority, evidence_type, symbol
               )"""
        )


def _repair_legacy_foreign_keys(db_path: Path) -> int:
    """Remove only metadata links whose referenced parent no longer exists."""
    with connect_rw(db_path) as conn:
        required = {"stock_exchange", "stocks", "exchanges"}
        present = {
            row["name"]
            for row in fetch_all(
                conn,
                """SELECT name FROM sqlite_master
                   WHERE type = 'table'""",
            )
        }
        if not required.issubset(present):
            return 0
        before = int(
            (
                fetch_one(
                    conn,
                    """SELECT COUNT(*) AS n
                       FROM stock_exchange se
                       WHERE NOT EXISTS (
                           SELECT 1 FROM stocks s
                           WHERE s.ticker = se.ticker
                       )
                       OR NOT EXISTS (
                           SELECT 1 FROM exchanges e
                           WHERE e.exchange = se.exchange
                       )""",
                )
                or {}
            ).get("n")
            or 0
        )
        if before:
            conn.execute(
                """DELETE FROM stock_exchange
                   WHERE NOT EXISTS (
                       SELECT 1 FROM stocks s
                       WHERE s.ticker = stock_exchange.ticker
                   )
                   OR NOT EXISTS (
                       SELECT 1 FROM exchanges e
                       WHERE e.exchange = stock_exchange.exchange
                   )"""
            )
            log.warning(
                "Removed %d orphan legacy stock_exchange rows", before
            )
        return before


def _mark_partial_latest_session_provisional(db_path: Path) -> int:
    """Quarantine a partial newest date left by an intraday legacy sync."""
    with connect_rw(db_path) as conn:
        latest = fetch_one(
            conn,
            """SELECT time, COUNT(DISTINCT symbol) AS symbols
               FROM stock_price_history
               WHERE time = (
                   SELECT MAX(time) FROM stock_price_history
                   WHERE typeof(time) = 'text'
               )
               GROUP BY time""",
        )
        if not latest or int(latest.get("symbols") or 0) >= 100:
            return 0
        price_date = str(latest["time"])
        cur = conn.execute(
            """INSERT INTO market_price_metadata
               (symbol, price_date, source, price_basis, raw_unit,
                is_provisional, observed_at)
               SELECT symbol, time, 'LEGACY_UNKNOWN', 'legacy_unknown',
                      'THOUSAND_VND', 1, CURRENT_TIMESTAMP
               FROM stock_price_history
               WHERE time = ?
               ON CONFLICT(symbol, price_date) DO NOTHING""",
            (price_date,),
        )
        return cur.rowcount


def _migrate_saved_portfolio(db_path: Path) -> None:
    """Import the old JSON strategy choice once, deliberately ignoring capital."""
    with connect_rw(db_path) as conn:
        existing = fetch_one(conn, "SELECT id FROM fund_preferences WHERE id = 1")
        if existing:
            return

        strategy = "LAST_8Q_PLUS"
        select_pct = 10.0
        old_path = db_path.parent / "saved_portfolio.json"
        if old_path.exists():
            try:
                old = json.loads(old_path.read_text(encoding="utf-8"))
                if old.get("strategy") in {"TTM_20Q", "LAST_8Q_PLUS"}:
                    strategy = old["strategy"]
                pct = float(old.get("select_pct", select_pct))
                if pct in {10.0, 12.0, 14.0, 16.0}:
                    select_pct = pct
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                log.warning("Could not import saved portfolio preferences")

        conn.execute(
            """INSERT INTO fund_preferences (id, strategy, select_pct)
               VALUES (1, ?, ?)""",
            (strategy, select_pct),
        )
