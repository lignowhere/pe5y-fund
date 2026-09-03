from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.data.db_migration import _create_legacy_reuse_tables
from backend.data.legacy_reuse import reconcile_vendor_research_versions


def _research_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE financial_data_versions (
                id INTEGER PRIMARY KEY,
                source TEXT,
                source_api TEXT,
                as_of_year INTEGER,
                as_of_quarter INTEGER,
                content_hash TEXT UNIQUE,
                row_count INTEGER,
                symbol_count INTEGER,
                is_active INTEGER,
                point_in_time_ready INTEGER,
                publication_coverage_pct REAL,
                verified_row_count INTEGER,
                methodology_version TEXT,
                official_provenance_ready INTEGER,
                quality_status TEXT,
                quality_issues_json TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE financial_ratio_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                financial_data_version_id INTEGER,
                symbol TEXT, period TEXT, year INTEGER, quarter INTEGER,
                price_to_book REAL, price_to_earnings REAL, eps_vnd REAL,
                bvps_vnd REAL, roe REAL, market_cap_billions REAL,
                shares_outstanding_millions REAL, data_json TEXT,
                source TEXT, public_date TEXT, source_created_at TEXT,
                source_updated_at TEXT, available_at TEXT,
                publication_status TEXT, observed_at TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE financial_ratios (
                symbol TEXT, period TEXT, year INTEGER, quarter INTEGER,
                price_to_book REAL, price_to_earnings REAL, eps_vnd REAL,
                bvps_vnd REAL, roe REAL, market_cap_billions REAL,
                shares_outstanding_millions REAL, data_json TEXT,
                source TEXT, public_date TEXT, source_created_at TEXT,
                source_updated_at TEXT, available_at TEXT,
                publication_status TEXT, financial_data_version_id INTEGER
            )"""
        )
        conn.executemany(
            """INSERT INTO financial_data_versions
               VALUES (?, 'VCI', 'test', 2025, 2, ?, ?, 1, ?, 0, 0, 0,
                       'vendor_publication_research_v2', 0, ?, '[]')""",
            [
                (1, "active", 2, 1, "vendor_research"),
                (2, "stored", 2, 0, "quarantined_vendor_research"),
            ],
        )
        rows = [
            (1, "AAA", "2025-Q1", 2025, 1, 100.0),
            (1, "DP3", "2017-Q4", 2017, 4, 50.0),
            (2, "AAA", "2025-Q1", 2025, 1, 999.0),
            (2, "DP3", "2025-Q1", 2025, 1, 80.0),
        ]
        conn.executemany(
            """INSERT INTO financial_ratio_versions
               (financial_data_version_id, symbol, period, year, quarter,
                eps_vnd, source, publication_status)
               VALUES (?, ?, ?, ?, ?, ?, 'VCI', 'legacy_unverified')""",
            rows,
        )
        conn.executemany(
            """INSERT INTO financial_ratios
               (symbol, period, year, quarter, eps_vnd, source,
                publication_status, financial_data_version_id)
               VALUES (?, ?, ?, ?, ?, 'VCI', 'legacy_unverified', 1)""",
            [(row[1], row[2], row[3], row[4], row[5]) for row in rows[:2]],
        )


def test_stored_vendor_union_fills_missing_without_overwrite(
    tmp_path: Path,
):
    db = tmp_path / "research.db"
    _research_db(db)
    result = reconcile_vendor_research_versions(db)
    assert result["missing_keys_recovered"] == 1
    assert result["point_in_time_ready"] is False
    assert result["official_provenance_ready"] is False
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            """SELECT symbol, year, quarter, eps_vnd
               FROM financial_ratios ORDER BY symbol, year"""
        ).fetchall()
        version = conn.execute(
            """SELECT point_in_time_ready, official_provenance_ready,
                      quality_status
               FROM financial_data_versions WHERE is_active = 1"""
        ).fetchone()
    assert rows == [
        ("AAA", 2025, 1, 100.0),
        ("DP3", 2017, 4, 50.0),
        ("DP3", 2025, 1, 80.0),
    ]
    assert version == (0, 0, "vendor_research_reconciled")


def test_legacy_queue_tables_are_separate_from_official_ledger(
    tmp_path: Path,
):
    db = tmp_path / "queue.db"
    _create_legacy_reuse_tables(db)
    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "legacy_verification_queue" in tables
    assert "financial_filing_revisions" not in tables
