from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.config import AppConfig
from backend.data.db_migration import (
    _create_fund_tables,
    _create_hardening_tables,
    _create_snapshot_tables,
)
from backend.data.financial_snapshot import (
    FinancialSnapshotError,
    activate_staged_financials,
)
from backend.data.provenance import (
    BenchmarkTotalReturnEvidence,
    FilingEvidence,
    PriceEvidence,
    ProvenanceError,
    activate_official_financial_version,
    import_benchmark_total_return,
    import_filing_revision,
    import_price_observation,
)
from backend.fund.market_data import (
    strategy_timing,
    verified_benchmark_total_return_pair,
)
from backend.fund.snapshots import (
    VERIFIED_LEDGER_BASIS,
    SnapshotCycleBuild,
    _backtest_cycle,
)
from backend.strategy.signal_pe_ttm_20q import (
    PE20QCandidate,
    _query_quarterly_eps,
)


def _price_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE stock_price_history (
            symbol TEXT NOT NULL,
            time TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            UNIQUE(symbol, time)
        )"""
    )


def test_market_timing_uses_completed_close_then_next_open(tmp_path: Path):
    db = tmp_path / "timing.db"
    with sqlite3.connect(db) as conn:
        _price_table(conn)
        conn.executemany(
            """INSERT INTO stock_price_history
               VALUES ('VNINDEX', ?, ?, ?, ?, ?, 1000)""",
            [
                ("2025-08-29", 1680, 1690, 1670, 1682),
                ("2025-09-03", 1690, 1700, 1680, 1695),
            ],
        )
    assert strategy_timing(db, "2025-09-01") == {
        "signal_price_date": "2025-08-29",
        "signal_cutoff": "2025-08-29T08:00:00Z",
        "execution_date": "2025-09-03",
    }


def test_future_revision_cannot_leak_before_cutoff(tmp_path: Path):
    db = tmp_path / "revision.db"
    with sqlite3.connect(db) as conn:
        _price_table(conn)
    _create_snapshot_tables(db)
    first_id = import_filing_revision(
        db,
        FilingEvidence(
            symbol="AAA",
            year=2025,
            quarter=2,
            statement_scope="consolidated",
            basic_eps_vnd=100,
            published_at="2025-08-20T10:00:00+07:00",
            first_observed_at="2025-08-20T10:05:00+07:00",
            availability_basis="official_timestamp",
            source_authority="HSX",
            source_url="https://example.test/aaa-q2",
            document_sha256="1" * 64,
            content_sha256="2" * 64,
        ),
    )
    second_id = import_filing_revision(
        db,
        FilingEvidence(
            symbol="AAA",
            year=2025,
            quarter=2,
            statement_scope="consolidated",
            basic_eps_vnd=900,
            published_at="2025-09-10T09:00:00+07:00",
            first_observed_at="2025-09-10T09:05:00+07:00",
            availability_basis="official_timestamp",
            source_authority="HSX",
            source_url="https://example.test/aaa-q2-restated",
            document_sha256="3" * 64,
            content_sha256="4" * 64,
        ),
    )
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        before = _query_quarterly_eps(
            conn,
            2025,
            2,
            quarter_count=1,
            require_all_positive=False,
            as_of_date="2025-08-29T08:00:00Z",
            require_official_provenance=True,
        )
        after = _query_quarterly_eps(
            conn,
            2025,
            2,
            quarter_count=1,
            require_all_positive=False,
            as_of_date="2025-09-11T08:00:00Z",
            require_official_provenance=True,
        )
        revisions = conn.execute(
            """SELECT id, available_at FROM financial_filing_revisions
               ORDER BY id"""
        ).fetchall()
    assert before[0]["avg_eps"] == 100
    assert after[0]["avg_eps"] == 900
    assert [row["id"] for row in revisions] == [first_id, second_id]
    assert revisions[0]["available_at"] == "2025-08-20T03:00:00Z"


def test_date_only_filing_is_available_next_market_session(tmp_path: Path):
    db = tmp_path / "date-only.db"
    with sqlite3.connect(db) as conn:
        _price_table(conn)
        conn.executemany(
            """INSERT INTO stock_price_history
               VALUES ('VNINDEX', ?, 1, 1, 1, 1, 1)""",
            [("2025-08-29",), ("2025-09-03",)],
        )
    _create_snapshot_tables(db)
    revision_id = import_filing_revision(
        db,
        FilingEvidence(
            symbol="AAA",
            year=2025,
            quarter=2,
            statement_scope="consolidated",
            basic_eps_vnd=100,
            published_at="2025-08-29",
            first_observed_at="2025-09-01T09:00:00+07:00",
            availability_basis="official_date_next_session",
            source_authority="HSX",
            source_url=None,
            document_sha256="5" * 64,
            content_sha256="6" * 64,
        ),
    )
    with sqlite3.connect(db) as conn:
        available_at = conn.execute(
            """SELECT available_at FROM financial_filing_revisions
               WHERE id = ?""",
            (revision_id,),
        ).fetchone()[0]
    assert available_at == "2025-09-02T17:00:00Z"


def test_dp3_source_empty_blocks_financial_promotion(tmp_path: Path):
    db = tmp_path / "dp3.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE stocks (
                ticker TEXT PRIMARY KEY, status TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE stock_exchange (
                ticker TEXT, exchange TEXT
            )"""
        )
        _price_table(conn)
        conn.execute(
            """CREATE TABLE financial_ratios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, period TEXT, year INTEGER, quarter INTEGER,
                price_to_book REAL, price_to_earnings REAL, eps_vnd REAL,
                bvps_vnd REAL, roe REAL, market_cap_billions REAL,
                shares_outstanding_millions REAL, data_json TEXT,
                source TEXT
            )"""
        )
        conn.executemany(
            "INSERT INTO stocks VALUES (?, 'listed')",
            [("AAA",), ("DP3",)],
        )
        conn.executemany(
            "INSERT INTO stock_exchange VALUES (?, 'HNX')",
            [("AAA",), ("DP3",)],
        )
        conn.executemany(
            """INSERT INTO stock_price_history
               VALUES (?, '2026-07-28', 10, 10, 10, 10, 1000)""",
            [("AAA",), ("DP3",)],
        )
    _create_fund_tables(db)
    _create_snapshot_tables(db)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            """INSERT INTO financial_sync_symbols
               (run_id, symbol, status, row_count)
               VALUES (7, ?, ?, ?)""",
            [("AAA", "ok", 1), ("DP3", "empty", 0)],
        )
        conn.executemany(
            """INSERT INTO financial_sync_symbol_history
               (run_id, symbol, status, row_count,
                required_for_investment)
               VALUES (7, ?, ?, ?, 1)""",
            [("AAA", "verified", 1), ("DP3", "source_empty", 0)],
        )
        conn.execute(
            """INSERT INTO financial_ratios_staging
               (run_id, symbol, period, year, quarter, eps_vnd,
                source, available_at, publication_status)
               VALUES (7, 'AAA', '2026-Q2', 2026, 2, 100,
                       'VCI', '2026-07-20', 'verified')"""
        )
    with pytest.raises(
        FinancialSnapshotError, match="DP3:source_empty"
    ):
        activate_staged_financials(
            db,
            7,
            as_of_year=2026,
            as_of_quarter=2,
            expected_symbols=2,
        )


def test_price_conflict_is_audited_and_not_overwritten(tmp_path: Path):
    db = tmp_path / "price-evidence.db"
    with sqlite3.connect(db) as conn:
        _price_table(conn)
    _create_snapshot_tables(db)
    _create_hardening_tables(db)
    original = PriceEvidence(
        symbol="AAA",
        price_date="2025-09-03",
        open_vnd=10_000,
        high_vnd=11_000,
        low_vnd=9_000,
        close_vnd=10_500,
        volume=1_000,
        source="VCI",
        source_url="https://example.test/prices",
        payload_sha256="7" * 64,
        observed_at="2025-09-03T17:00:00+07:00",
    )
    import_price_observation(db, original)
    with pytest.raises(ProvenanceError, match="conflicts"):
        import_price_observation(
            db,
            PriceEvidence(
                    **{
                        **original.__dict__,
                        "open_vnd": 12_000,
                        "high_vnd": 13_000,
                        "payload_sha256": "8" * 64,
                }
            ),
        )
    with sqlite3.connect(db) as conn:
        open_price = conn.execute(
            """SELECT open FROM stock_price_history
               WHERE symbol = 'AAA' AND time = '2025-09-03'"""
        ).fetchone()[0]
        statuses = [
            row[0]
            for row in conn.execute(
                """SELECT verification_status
                   FROM price_source_observations ORDER BY id"""
            )
        ]
    assert open_price == 10.0
    assert statuses == ["verified", "conflict"]


def test_verified_benchmark_total_return_rejects_conflict(
    tmp_path: Path,
):
    db = tmp_path / "benchmark.db"
    with sqlite3.connect(db) as conn:
        _price_table(conn)
    _create_snapshot_tables(db)
    base = {
        "symbol": "VNINDEX",
        "source_authority": "HSX",
        "source_url": "https://example.test/vnindex-tr",
        "observed_at": "2026-09-01T17:00:00+07:00",
    }
    import_benchmark_total_return(
        db,
        BenchmarkTotalReturnEvidence(
            **base,
            price_date="2025-09-03",
            index_value=1000,
            document_sha256="9" * 64,
        ),
    )
    import_benchmark_total_return(
        db,
        BenchmarkTotalReturnEvidence(
            **base,
            price_date="2026-09-01",
            index_value=1100,
            document_sha256="a" * 64,
        ),
    )
    pair = verified_benchmark_total_return_pair(
        db, "VNINDEX", "2025-09-03", "2026-09-01"
    )
    assert pair is not None
    assert pair["end_value"] / pair["start_value"] == 1.1

    import_benchmark_total_return(
        db,
        BenchmarkTotalReturnEvidence(
            **base,
            price_date="2026-09-01",
            index_value=1090,
            document_sha256="b" * 64,
            verification_status="conflict",
        ),
    )
    assert (
        verified_benchmark_total_return_pair(
            db, "VNINDEX", "2025-09-03", "2026-09-01"
        )
        is None
    )


def test_authoritative_backtest_uses_share_ledger_and_verified_benchmark(
    tmp_path: Path,
):
    db = tmp_path / "ledger-backtest.db"
    with sqlite3.connect(db) as conn:
        _price_table(conn)
        conn.execute(
            """INSERT INTO stock_price_history
               VALUES ('AAA', '2026-09-01', 6, 6, 6, 6, 1000000)"""
        )
    _create_snapshot_tables(db)
    _create_hardening_tables(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            """INSERT INTO market_price_metadata
               (symbol, price_date, source, price_basis, raw_unit,
                is_provisional, observed_at, source_url,
                source_payload_sha256)
               VALUES ('AAA', '2026-09-01', 'VCI',
                       'execution_unadjusted', 'THOUSAND_VND', 0,
                       '2026-09-01T10:00:00Z',
                       'https://example.test/prices', ?)""",
            ("c" * 64,),
        )
        conn.execute(
            """INSERT INTO price_source_observations
               (symbol, price_date, open_vnd, high_vnd, low_vnd,
                close_vnd, volume, source, payload_sha256, observed_at,
                is_session_final, verification_status)
               VALUES ('AAA', '2026-09-01', 6000, 6000, 6000, 6000,
                       1000000, 'VCI', ?, '2026-09-01T10:00:00Z',
                       1, 'verified')""",
            ("c" * 64,),
        )
        conn.execute(
            """INSERT INTO corporate_action_coverage
               (symbol, start_date, end_date, coverage_status,
                source_authority, document_sha256, observed_at)
               VALUES ('AAA', '2025-09-03', '2026-09-01', 'verified',
                       'HSX', ?, '2026-09-01T10:00:00Z')""",
            ("d" * 64,),
        )
        conn.executemany(
            """INSERT INTO corporate_actions
               (symbol, action_type, ex_date, payment_date,
                cash_vnd_per_share, share_factor, source_authority,
                document_sha256, verification_status, observed_at)
               VALUES ('AAA', ?, '2026-01-02', ?, ?, ?, 'HSX', ?,
                       'verified', '2026-01-02T10:00:00Z')""",
            [
                (
                    "cash_dividend",
                    "2026-01-20",
                    1000,
                    None,
                    "e" * 64,
                ),
                ("split", None, None, 2, "f" * 64),
            ],
        )
    for price_date, value, digest in (
        ("2025-09-03", 1000, "1" * 64),
        ("2026-09-01", 1100, "2" * 64),
    ):
        import_benchmark_total_return(
            db,
            BenchmarkTotalReturnEvidence(
                symbol="VNINDEX",
                price_date=price_date,
                index_value=value,
                source_authority="HSX",
                document_sha256=digest,
                observed_at=f"{price_date}T10:00:00Z",
            ),
        )
    candidate = PE20QCandidate(
        symbol="AAA",
        avg_eps_20q=1000,
        pe_ttm_20q=10,
        market_cap_vnd=1_000_000_000,
        signal_rank=1,
        buy_price_vnd=10_000,
        quarters_count=20,
    )
    cycle = SnapshotCycleBuild(
        strategy="LAST_8Q_PLUS",
        select_pct=10,
        formation_year=2024,
        hold_year=2025,
        rebalance_date="2025-09-01",
        signal_cutoff="2025-08-29T08:00:00Z",
        signal_price_date="2025-08-29",
        execution_date="2025-09-03",
        quarter_count=20,
        pit_tier="strict_pit",
        universe_count=100,
        selected=[candidate],
        rebalance_prices={
            "AAA": {
                "price_vnd": 10_000,
                "price_date": "2025-09-03",
                "source": "VCI",
            }
        },
        adv_shares={"AAA": 10_000_000},
        data_checksum="0" * 64,
    )
    result = _backtest_cycle(
        AppConfig(db_path=db),
        cycle,
        100_000_000,
        "2026-09-01",
        {},
        VERIFIED_LEDGER_BASIS,
    )
    assert result["valuation_date"] == "2026-09-01"
    assert result["benchmark_return"] == pytest.approx(0.1)
    assert result["return"] > 0.25


def test_official_financial_promotion_requires_complete_classification(
    tmp_path: Path,
):
    db = tmp_path / "official-promotion.db"
    with sqlite3.connect(db) as conn:
        _price_table(conn)
        conn.execute(
            "CREATE TABLE stocks (ticker TEXT PRIMARY KEY, status TEXT)"
        )
        conn.execute(
            "CREATE TABLE stock_exchange (ticker TEXT, exchange TEXT)"
        )
        conn.executemany(
            "INSERT INTO stocks VALUES (?, 'listed')",
            [("AAA",), ("DP3",)],
        )
        conn.executemany(
            "INSERT INTO stock_exchange VALUES (?, 'HNX')",
            [("AAA",), ("DP3",)],
        )
        conn.executemany(
            """INSERT INTO stock_price_history
               VALUES (?, '2026-07-28', 10, 10, 10, 10, 1000)""",
            [("AAA",), ("DP3",)],
        )
    _create_snapshot_tables(db)
    import_filing_revision(
        db,
        FilingEvidence(
            symbol="AAA",
            year=2025,
            quarter=2,
            statement_scope="consolidated",
            basic_eps_vnd=100,
            published_at="2025-08-20T10:00:00+07:00",
            first_observed_at="2025-08-20T10:05:00+07:00",
            availability_basis="official_timestamp",
            source_authority="HNX",
            source_url="https://example.test/aaa",
            document_sha256="3" * 64,
            content_sha256="4" * 64,
        ),
    )
    with sqlite3.connect(db) as conn:
        first_batch = conn.execute(
            """INSERT INTO official_provenance_batches
               (manifest_sha256, as_of_year, as_of_quarter,
                classification_cutoff, source_authority, observed_at)
               VALUES (?, 2025, 2, '2025-08-29T08:00:00Z',
                       'HNX', '2026-07-29T10:00:00Z')""",
            ("5" * 64,),
        ).lastrowid
        conn.executemany(
            """INSERT INTO official_symbol_classifications
               (batch_id, symbol, status, source_authority,
                document_sha256, observed_at)
               VALUES (?, ?, ?, 'HNX', ?,
                       '2026-07-29T10:00:00Z')""",
            [
                (first_batch, "AAA", "verified", "6" * 64),
                (first_batch, "DP3", "source_empty", "7" * 64),
            ],
        )
    with pytest.raises(ProvenanceError, match="DP3:source_empty"):
        activate_official_financial_version(db, first_batch)

    with sqlite3.connect(db) as conn:
        second_batch = conn.execute(
            """INSERT INTO official_provenance_batches
               (manifest_sha256, as_of_year, as_of_quarter,
                classification_cutoff, source_authority, observed_at)
               VALUES (?, 2025, 2, '2025-08-29T08:00:00Z',
                       'HNX', '2026-07-29T11:00:00Z')""",
            ("8" * 64,),
        ).lastrowid
        conn.executemany(
            """INSERT INTO official_symbol_classifications
               (batch_id, symbol, status, source_authority,
                document_sha256, observed_at)
               VALUES (?, ?, ?, 'HNX', ?,
                       '2026-07-29T11:00:00Z')""",
            [
                (second_batch, "AAA", "verified", "9" * 64),
                (second_batch, "DP3", "not_published", "a" * 64),
            ],
        )
    promoted = activate_official_financial_version(db, second_batch)
    assert promoted["point_in_time_ready"] is True
    assert promoted["required_symbol_count"] == 2
    with sqlite3.connect(db) as conn:
        active = conn.execute(
            """SELECT source, point_in_time_ready,
                      official_provenance_ready, provenance_batch_id
               FROM financial_data_versions WHERE is_active = 1"""
        ).fetchone()
    assert active == ("OFFICIAL", 1, 1, second_batch)
