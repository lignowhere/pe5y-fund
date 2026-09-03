"""Tests for backtested immutable strategy-cycle snapshots."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import pytest

from backend.config import AppConfig, StrategyConfig
from backend.data.db_migration import (
    _create_hardening_tables,
    _create_snapshot_tables,
)
from backend.fund.cycle import resolve_active_cycle
from backend.fund.snapshots import (
    build_and_activate_snapshot_set,
    get_active_snapshot_status,
    load_active_cycle_snapshot,
    strategy_config_fingerprint,
)


@pytest.fixture
def strategy_snapshot_db(tmp_path: Path) -> tuple[Path, AppConfig]:
    db = tmp_path / "strategy-snapshot.db"
    symbols = [f"A{index:02d}" for index in range(20)]
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE financial_ratios (
                symbol TEXT,
                period TEXT,
                year INTEGER,
                quarter INTEGER,
                eps_vnd REAL,
                market_cap_billions REAL
            )"""
        )
        conn.execute(
            """CREATE TABLE stock_price_history (
                symbol TEXT,
                time TEXT,
                open REAL,
                close REAL,
                volume INTEGER
            )"""
        )
        for index, symbol in enumerate(symbols):
            for year, quarters in [
                (2020, [3, 4]),
                (2021, [1, 2, 3, 4]),
                (2022, [1, 2, 3, 4]),
                (2023, [1, 2, 3, 4]),
                (2024, [1, 2, 3, 4]),
                (2025, [1, 2]),
            ]:
                for quarter in quarters:
                    conn.execute(
                        """INSERT INTO financial_ratios
                           (symbol, period, year, quarter, eps_vnd)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            symbol,
                            f"{year}-Q{quarter}",
                            year,
                            quarter,
                            500 + index * 50,
                        ),
                    )
            conn.execute(
                """INSERT INTO financial_ratios
                   (symbol, period, year, quarter, market_cap_billions)
                   VALUES (?, '2024', 2024, NULL, ?)""",
                (symbol, 1_000_000_000_000),
            )
            for day in range(1, 26):
                conn.execute(
                    """INSERT INTO stock_price_history
                       (symbol, time, close, volume)
                       VALUES (?, ?, ?, 1000000)""",
                    (
                        symbol,
                        f"2024-08-{day:02d}",
                        20 + index + day / 100,
                    ),
                )
            for day in range(1, 26):
                conn.execute(
                    """INSERT INTO stock_price_history
                       (symbol, time, close, volume)
                       VALUES (?, ?, ?, 1000000)""",
                    (
                        symbol,
                        f"2025-08-{day:02d}",
                        25 + index + day / 100,
                    ),
                )
            conn.execute(
                """INSERT INTO stock_price_history
                   (symbol, time, open, close, volume)
                   VALUES (?, '2025-08-29', ?, ?, 1000000)""",
                (symbol, 29 + index, 29 + index),
            )
            conn.executemany(
                """INSERT INTO stock_price_history
                   (symbol, time, open, close, volume)
                   VALUES (?, ?, ?, ?, 1000000)""",
                [
                    (symbol, "2025-09-03", 30 + index, 31 + index),
                    (symbol, "2026-07-28", 35 + index, 35 + index),
                    (symbol, "2026-09-01", 40 + index, 40 + index),
                ],
            )
        conn.executemany(
            """INSERT INTO stock_price_history
               (symbol, time, open, close, volume)
               VALUES ('VNINDEX', ?, ?, ?, 1000000)""",
            [
                ("2025-08-29", 1_680, 1_682),
                ("2025-09-03", 1_690, 1_695),
                ("2026-07-28", 1_850, 1_850),
                ("2026-09-01", 1_900, 1_900),
            ],
        )
    _create_snapshot_tables(db)
    _create_hardening_tables(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            """INSERT INTO financial_data_versions
               (source, source_api, as_of_year, as_of_quarter, content_hash,
                row_count, symbol_count, is_active, point_in_time_ready,
                publication_coverage_pct, verified_row_count,
                methodology_version, official_provenance_ready,
                quality_status)
               VALUES ('VCI', 'test', 2026, 2, ?, 420, 20, 1, 1,
                       100, 420, 'official_revision_pit_v2', 1,
                       'official_verified')""",
            ("a" * 64,),
        )
        conn.execute(
            """INSERT INTO financial_ratio_versions
               (financial_data_version_id, symbol, period, year, quarter,
                eps_vnd, market_cap_billions, source, available_at,
                publication_status)
               SELECT 1, symbol, period, year, quarter, eps_vnd,
                      market_cap_billions, 'VCI', '2025-08-20', 'verified'
               FROM financial_ratios"""
        )
        for index, symbol in enumerate(symbols):
            quarter_rows = conn.execute(
                """SELECT year, quarter, eps_vnd
                   FROM financial_ratios
                   WHERE symbol = ? AND quarter BETWEEN 1 AND 4
                   ORDER BY year, quarter""",
                (symbol,),
            ).fetchall()
            for year, quarter, eps_vnd in quarter_rows:
                content_hash = (
                    f"{symbol}:{year}:Q{quarter}:v1".encode("utf-8").hex()
                )[:64].ljust(64, "0")
                cursor = conn.execute(
                    """INSERT INTO financial_filing_revisions
                       (symbol, year, quarter, statement_scope,
                        revision_number, basic_eps_vnd, published_at,
                        first_observed_at, available_at,
                        availability_basis, source_authority, source_url,
                        document_sha256, content_sha256,
                        verification_status)
                       VALUES (?, ?, ?, 'consolidated', 1, ?,
                               '2025-08-20T10:00:00+07:00',
                               '2025-08-20T10:00:00+07:00',
                               '2025-08-20T10:00:00+07:00',
                               'official_timestamp', 'HSX',
                               'https://example.test/filing',
                               ?, ?, 'verified')""",
                    (
                        symbol,
                        year,
                        quarter,
                        eps_vnd,
                        "d" * 64,
                        content_hash,
                    ),
                )
                conn.execute(
                    """INSERT INTO financial_period_facts
                       (filing_revision_id, symbol, year, quarter,
                        basic_eps_vnd, is_independent_quarter,
                        extractor_version)
                       VALUES (?, ?, ?, ?, ?, 1, 'fixture-v1')""",
                    (
                        cursor.lastrowid,
                        symbol,
                        year,
                        quarter,
                        eps_vnd,
                    ),
                )
            conn.execute(
                """INSERT INTO shares_outstanding_history
                   (symbol, effective_from, shares_outstanding,
                    source_authority, source_url, document_sha256,
                    verification_status, observed_at)
                   VALUES (?, '2020-01-01', ?, 'HSX',
                           'https://example.test/shares', ?,
                           'verified', '2025-08-20T10:00:00+07:00')""",
                (symbol, 100_000_000 + index, "s" * 64),
            )
            conn.execute(
                """INSERT INTO market_price_metadata
                   (symbol, price_date, source, price_basis, raw_unit,
                    is_provisional, observed_at, source_url,
                    source_payload_sha256)
                   VALUES (?, ?, 'TEST', 'execution_unadjusted',
                           'THOUSAND_VND', 0, ?,
                           'https://example.test/prices', ?)""",
                (
                    symbol,
                    "2025-08-29",
                    "2025-08-29T08:30:00Z",
                    "1" * 64,
                ),
            )
            conn.execute(
                """INSERT INTO price_source_observations
                   (symbol, price_date, open_vnd, high_vnd, low_vnd,
                    close_vnd, volume, source, payload_sha256,
                    observed_at, is_session_final, verification_status)
                   VALUES (?, '2025-08-29', ?, ?, ?, ?, 1000000,
                           'TEST', ?, '2025-08-29T08:30:00Z', 1,
                           'verified')""",
                (
                    symbol,
                    (29 + index) * 1000,
                    (29 + index) * 1000,
                    (29 + index) * 1000,
                    (29 + index) * 1000,
                    "1" * 64,
                ),
            )
            conn.execute(
                """INSERT INTO market_price_metadata
                   (symbol, price_date, source, price_basis, raw_unit,
                    is_provisional, observed_at, source_url,
                    source_payload_sha256)
                   VALUES (?, ?, 'TEST', 'execution_unadjusted',
                           'THOUSAND_VND', 0, ?,
                           'https://example.test/prices', ?)""",
                (
                    symbol,
                    "2025-09-03",
                    "2025-09-03T09:00:00Z",
                    "2" * 64,
                ),
            )
            conn.execute(
                """INSERT INTO price_source_observations
                   (symbol, price_date, open_vnd, high_vnd, low_vnd,
                    close_vnd, volume, source, payload_sha256,
                    observed_at, is_session_final, verification_status)
                   VALUES (?, '2025-09-03', ?, ?, ?, ?, 1000000,
                           'TEST', ?, '2025-09-03T09:00:00Z', 1,
                           'verified')""",
                (
                    symbol,
                    (30 + index) * 1000,
                    (31 + index) * 1000,
                    (30 + index) * 1000,
                    (31 + index) * 1000,
                    "2" * 64,
                ),
            )
            conn.execute(
                """INSERT INTO corporate_action_coverage
                   (symbol, start_date, end_date, coverage_status,
                    source_authority, document_sha256, observed_at)
                   VALUES (?, '2025-09-03', '2026-09-01', 'verified',
                           'HSX', ?, '2026-09-01T10:00:00Z')""",
                (symbol, "c" * 64),
            )

    strategy = StrategyConfig(
        select_pcts=[10.0],
        min_trading_days=5,
        min_avg_dollar_volume_vnd=0,
        max_zero_volume_frac=1,
        max_stale_close_frac=1,
        mcap_base_vnd=0,
    )
    return db, AppConfig(db_path=db, strategy=strategy)


def test_snapshot_build_backtests_and_cycle_is_immutable(
    strategy_snapshot_db: tuple[Path, AppConfig],
):
    db, config = strategy_snapshot_db
    result = build_and_activate_snapshot_set(
        config,
        formation_years=[2024],
        strategies=["LAST_8Q_PLUS"],
        select_pcts=[10],
    )
    assert result["cycle_count"] == 1
    assert result["backtests"][0]["start_hold_year"] == 2025

    snapshot = load_active_cycle_snapshot(
        db, "LAST_8Q_PLUS", 10, 2025
    )
    assert snapshot is not None
    assert snapshot["selected_count"] == 15
    assert len(snapshot["items"]) == 15
    status = get_active_snapshot_status(db)
    assert status is not None
    assert status["backtests"][0]["net_cagr"] > -1
    assert {row["pit_tier"] for row in status["backtests"]} == {
        "strict_pit",
        "legacy_research",
    }
    assert snapshot["quarter_count"] == 20
    assert snapshot["pit_tier"] == "strict_pit"
    fingerprint_json, _ = strategy_config_fingerprint(config)
    assert json.loads(fingerprint_json)["strategy_variants"][
        "LAST_8Q_PLUS"
    ] == {
        "max_quarters": 20,
        "require_all_positive": False,
        "require_last_n_positive": 8,
    }

    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                """UPDATE strategy_cycle_snapshot_items
                   SET signal_rank = 99 WHERE cycle_snapshot_id = ?""",
                (snapshot["id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                """INSERT INTO strategy_cycle_snapshot_items
                   (cycle_snapshot_id, symbol, signal_rank, avg_eps_20q,
                    pe_ttm_20q, market_cap_vnd, quarters_count,
                    rebalance_price_vnd, rebalance_price_date,
                    adv_20d_shares, initial_weight)
                   VALUES (?, 'ZZZ', 99, 1, 1, 1, 20, 1000,
                           '2025-09-01', 1000, 0.1)""",
                (snapshot["id"],),
            )


def test_resolver_reads_snapshot_instead_of_recalculating(
    strategy_snapshot_db: tuple[Path, AppConfig],
):
    _, config = strategy_snapshot_db
    build_and_activate_snapshot_set(
        config,
        formation_years=[2024],
        strategies=["LAST_8Q_PLUS"],
        select_pcts=[10],
    )
    cycle = resolve_active_cycle(
        config,
        "LAST_8Q_PLUS",
        10,
        today=dt.date(2026, 7, 28),
    )
    assert cycle.snapshot_id is not None
    assert len(cycle.symbols) == 15
    assert cycle.rebalance_prices is not None
    assert cycle.adv_shares is not None
