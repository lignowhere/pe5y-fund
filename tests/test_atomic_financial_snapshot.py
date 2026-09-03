"""Tests for all-or-nothing, single-source financial activation."""
from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from backend.data.db_migration import (
    _create_fund_tables,
    _create_snapshot_tables,
)
from backend.data.financial_snapshot import (
    FinancialSnapshotError,
    activate_staged_financials,
    stage_vci_financials,
)
from backend.data.vci_client import VCIFinancialRow


@pytest.fixture
def snapshot_db(tmp_path: Path) -> Path:
    db = tmp_path / "atomic.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE stocks (ticker TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE stock_exchange (ticker TEXT, exchange TEXT)"
        )
        conn.execute(
            """CREATE TABLE financial_ratios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                source TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """INSERT INTO financial_ratios
               (symbol, period, year, quarter, eps_vnd, source)
               VALUES ('OLD', '2024-Q1', 2024, 1, 123, 'VCI')"""
        )
        conn.execute(
            """INSERT INTO financial_ratios
               (symbol, period, year, quarter, eps_vnd, source)
               VALUES ('LEG', '2017-Q4', 2017, 4, 456, 'VCI')"""
        )
    _create_fund_tables(db)
    _create_snapshot_tables(db)
    return db


class FakeVCI:
    def __init__(self, fail_symbol: str | None = None):
        self.fail_symbol = fail_symbol

    def get_all_financial_ratios(self, symbol: str):
        if symbol == self.fail_symbol:
            raise RuntimeError("network stopped")
        return [
            VCIFinancialRow(
                symbol=symbol,
                year=2024,
                quarter=None,
                public_date="2025-03-20",
                source_created_at="2025-03-20T08:00:00",
                source_updated_at="2025-03-20T08:00:00",
                eps=Decimal("4000"),
                ev=Decimal("1000"),
            ),
            VCIFinancialRow(
                symbol=symbol,
                year=2024,
                quarter=1,
                public_date="2024-04-25",
                source_created_at="2024-04-25T08:00:00",
                source_updated_at="2024-04-25T08:00:00",
                eps=Decimal("1000"),
                ev=Decimal("1000"),
            ),
        ]


def _new_run(db: Path) -> int:
    with sqlite3.connect(db) as conn:
        return int(conn.execute(
            "INSERT INTO data_sync_runs(status, stage) VALUES ('running', 'financials')"
        ).lastrowid)


def test_failed_stage_does_not_change_live_financials(snapshot_db: Path):
    run_id = _new_run(snapshot_db)
    stats = stage_vci_financials(
        snapshot_db, run_id, ["AAA", "BBB"], FakeVCI("BBB")
    )
    assert stats["failed"] == 1

    with pytest.raises(FinancialSnapshotError, match="failed symbols"):
        activate_staged_financials(
            snapshot_db,
            run_id,
            as_of_year=2026,
            as_of_quarter=2,
            expected_symbols=2,
        )

    with sqlite3.connect(snapshot_db) as conn:
        rows = conn.execute(
            "SELECT symbol, eps_vnd FROM financial_ratios"
        ).fetchall()
    assert rows == [("OLD", 123.0), ("LEG", 456.0)]


def test_complete_stage_replaces_live_data_in_one_version(snapshot_db: Path):
    run_id = _new_run(snapshot_db)
    stats = stage_vci_financials(
        snapshot_db, run_id, ["AAA", "BBB"], FakeVCI()
    )
    assert stats == {
        "total": 2,
        "rows_staged": 4,
        "failed": 0,
        "empty": 0,
    }
    result = activate_staged_financials(
        snapshot_db,
        run_id,
        as_of_year=2026,
        as_of_quarter=2,
        expected_symbols=2,
    )

    with sqlite3.connect(snapshot_db) as conn:
        symbols = [
            row[0] for row in conn.execute(
                "SELECT DISTINCT symbol FROM financial_ratios ORDER BY symbol"
            )
        ]
        active = conn.execute(
            """SELECT id, source, is_active, point_in_time_ready
               FROM financial_data_versions
               WHERE is_active = 1"""
        ).fetchone()
        versions = conn.execute(
            """SELECT COUNT(*), MIN(publication_status), MAX(available_at)
               FROM financial_ratio_versions"""
        ).fetchone()
    assert symbols == ["AAA", "BBB", "LEG"]
    assert active == (result.version_id, "VCI", 1, 0)
    assert versions == (5, "legacy_unverified", "2025-03-20")
    assert result.verified_row_count == 4
    assert result.publication_coverage_pct == 100
    assert len(result.content_hash) == 64

    with sqlite3.connect(snapshot_db) as conn:
        with pytest.raises(
            sqlite3.IntegrityError, match="immutable"
        ):
            conn.execute(
                """UPDATE financial_ratio_versions
                   SET eps_vnd = eps_vnd + 1
                   WHERE financial_data_version_id = ?""",
                (result.version_id,),
            )
