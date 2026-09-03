"""Regression tests for long-gap detection, fallback, and sync locking."""
from __future__ import annotations

import sqlite3
import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.config import AppConfig
from backend.data.financial_updater import update_financials_stream
from backend.data.vci_client import VCIClient
from backend.data.sync_service import (
    SyncBusyError,
    _financial_refresh_due,
    _process_lock,
    refresh_portfolio_prices,
)
from backend.data.sync_runner import _exit_code_for_result
from backend.data.updater import detect_missing_prices


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"status": "completed"}, 0),
        ({"status": "already_running"}, 0),
        ({"status": "failed"}, 1),
        ({"status": "completed", "prices_failed": 1}, 2),
        ({"status": "completed", "financials_failed": 2}, 2),
    ],
)
def test_sync_runner_exit_code_requests_retry_for_partial_failure(
    result: dict[str, object],
    expected: int,
):
    assert _exit_code_for_result(result) == expected


def test_vendor_freshness_is_independent_of_strict_pit_gate():
    version = {
        "as_of_year": 2026,
        "as_of_quarter": 2,
        "created_at": "2026-07-29 12:00:00",
        "point_in_time_ready": 0,
        "official_provenance_ready": 0,
    }
    assert not _financial_refresh_due(
        version, today=dt.date(2026, 7, 29)
    )


def test_vendor_freshness_detects_a_completed_quarter_gap():
    version = {
        "as_of_year": 2026,
        "as_of_quarter": 1,
        "created_at": "2026-07-29 12:00:00",
    }
    assert _financial_refresh_due(
        version, today=dt.date(2026, 7, 29)
    )


def test_missing_prices_includes_never_loaded_and_over_30_days(tmp_path: Path):
    db = tmp_path / "prices.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE stocks (ticker TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE stock_exchange (ticker TEXT, exchange TEXT)"
        )
        conn.execute(
            """CREATE TABLE stock_price_history (
                symbol TEXT, time TEXT, close REAL, volume INTEGER
            )"""
        )
        conn.execute(
            """CREATE TABLE market_price_metadata (
                symbol TEXT, price_date TEXT, source TEXT,
                price_basis TEXT, raw_unit TEXT, is_provisional INTEGER,
                observed_at TEXT, source_url TEXT,
                source_payload_sha256 TEXT,
                PRIMARY KEY(symbol, price_date)
            )"""
        )
        conn.executemany(
            "INSERT INTO stocks VALUES (?)",
            [("AAA",), ("BBB",), ("CCC",)],
        )
        conn.executemany(
            "INSERT INTO stock_exchange VALUES (?, 'HSX')",
            [("AAA",), ("BBB",), ("CCC",)],
        )
        conn.executemany(
            "INSERT INTO stock_price_history VALUES (?, ?, 10, 1000)",
            [
                ("AAA", "2026-07-28"),
                ("AAA", "2026-07-27"),
                ("BBB", "2026-05-01"),
            ],
        )

    missing = detect_missing_prices(
        db,
        min_trading_day_gap=1,
        min_symbols_for_market_day=1,
    )
    assert missing == ["BBB", "CCC"]


def test_missing_prices_does_not_accept_unverified_adjusted_rows(
    tmp_path: Path,
):
    db = tmp_path / "verified-prices.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE stocks (ticker TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE stock_exchange (ticker TEXT, exchange TEXT)"
        )
        conn.execute(
            """CREATE TABLE stock_price_history (
                symbol TEXT, time TEXT, close REAL, volume INTEGER
            )"""
        )
        conn.execute(
            """CREATE TABLE market_price_metadata (
                symbol TEXT, price_date TEXT, source TEXT,
                price_basis TEXT, raw_unit TEXT, is_provisional INTEGER,
                observed_at TEXT, source_url TEXT,
                source_payload_sha256 TEXT,
                PRIMARY KEY(symbol, price_date)
            )"""
        )
        conn.execute(
            """CREATE TABLE price_source_observations (
                symbol TEXT, price_date TEXT, source TEXT,
                payload_sha256 TEXT, is_session_final INTEGER,
                verification_status TEXT
            )"""
        )
        conn.executemany(
            "INSERT INTO stocks VALUES (?)", [("AAA",), ("BBB",)]
        )
        conn.executemany(
            "INSERT INTO stock_exchange VALUES (?, 'HSX')",
            [("AAA",), ("BBB",)],
        )
        conn.executemany(
            "INSERT INTO stock_price_history VALUES (?, ?, 10, 1000)",
            [
                ("AAA", "2026-07-29"),
                ("BBB", "2026-07-29"),
                ("BBB", "2026-07-28"),
            ],
        )
        digest = "a" * 64
        conn.executemany(
            """INSERT INTO market_price_metadata
               VALUES (?, ?, ?, 'current_spot', 'THOUSAND_VND',
                       0, '2026-07-30T00:00:00Z',
                       'https://example.test/prices', ?)""",
            [
                ("AAA", "2026-07-29", "VCI", digest),
                ("BBB", "2026-07-29", "VCI_GAP_CHART", digest),
                ("BBB", "2026-07-28", "VCI", digest),
            ],
        )
        conn.executemany(
            """INSERT INTO price_source_observations
               VALUES (?, ?, 'VCI', ?, 1, 'verified')""",
            [
                ("AAA", "2026-07-29", digest),
                ("BBB", "2026-07-28", digest),
            ],
        )

    missing = detect_missing_prices(
        db,
        min_trading_day_gap=1,
        min_symbols_for_market_day=1,
    )
    assert missing == ["BBB"]


def test_targeted_refresh_refuses_to_overlap_another_sync(tmp_path: Path):
    db = tmp_path / "locked.db"
    db.touch()
    with _process_lock(db) as acquired:
        assert acquired is True
        with pytest.raises(SyncBusyError):
            refresh_portfolio_prices(AppConfig(db_path=db), ["FPT"])


def test_targeted_refresh_skips_vendor_when_required_prices_are_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    db = tmp_path / "current.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE stock_price_history (
                symbol TEXT, time TEXT, close REAL, volume INTEGER
            )"""
        )
        conn.execute(
            """CREATE TABLE market_price_metadata (
                symbol TEXT, price_date TEXT, source TEXT,
                price_basis TEXT, raw_unit TEXT, is_provisional INTEGER,
                observed_at TEXT, source_url TEXT,
                source_payload_sha256 TEXT,
                PRIMARY KEY(symbol, price_date)
            )"""
        )
        import datetime as dt

        today = dt.date.today().isoformat()
        conn.executemany(
            "INSERT INTO stock_price_history VALUES (?, ?, 10, 1000)",
            [(f"S{i:03d}", today) for i in range(100)] + [("FPT", today)],
        )
        conn.executemany(
            """INSERT INTO market_price_metadata
               VALUES (?, ?, 'VCI', 'current_spot', 'THOUSAND_VND',
                       0, ?, 'https://example.test/prices', ?)""",
            [
                (symbol, today, f"{today}T10:00:00Z", "a" * 64)
                for symbol in [*(f"S{i:03d}" for i in range(100)), "FPT"]
            ],
        )

    class UnexpectedClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("fresh portfolio prices must not call a vendor")

    monkeypatch.setattr(
        "backend.data.sync_service.VCIClient", UnexpectedClient
    )
    assert refresh_portfolio_prices(
        AppConfig(db_path=db), ["FPT"]
    ) == []


def test_financial_update_uses_kbs_when_vci_is_empty(tmp_path: Path):
    db = tmp_path / "financials.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE financial_ratios (
                symbol TEXT,
                period TEXT,
                year INTEGER,
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
                UNIQUE(symbol, period)
            )"""
        )

    class EmptyVCI:
        def get_annual_ratios(self, symbol: str):
            return []

        def get_quarterly_ratios(self, symbol: str):
            return []

    class WorkingKBS:
        def get_financial_summary(self, symbol: str):
            return SimpleNamespace(
                symbol=symbol,
                year=2025,
                eps=5_000,
                pe=20,
                pb=4,
                roe=18,
                revenue=1,
                net_profit=1,
                bvps=25_000,
            )

    progress = list(
        update_financials_stream(
            db, ["FPT"], EmptyVCI(), WorkingKBS(), target_year=2025
        )
    )
    assert progress[0].status == "ok"
    assert progress[0].source == "KBS"
    with sqlite3.connect(db) as conn:
        source = conn.execute(
            "SELECT source FROM financial_ratios WHERE symbol = 'FPT'"
        ).fetchone()[0]
    assert source == "KBS"


def test_vietcap_iq_parser_keeps_market_cap_and_shares_in_raw_units():
    rows = VCIClient._parse_financial_rows(
        "FPT",
        {
            "years": [
                {
                    "yearReport": 2025,
                    "lengthReport": 5,
                    "publicDate": "2026-03-20T00:00:00",
                    "createDate": "2026-03-18T08:00:00",
                    "updateDate": "2026-04-02T09:15:00",
                    "isa23": 5000,
                }
            ]
        },
        [
            {
                "year": 2025,
                "quarter": 5,
                "marketCap": 150_000_000_000_000,
                "numberOfSharesMktCap": 1_500_000_000,
            }
        ],
        period="Y",
    )
    assert float(rows[0].ev) == 150_000_000_000_000
    assert float(rows[0].issue_share) == 1_500_000_000
    assert rows[0].public_date == "2026-03-20"
    assert rows[0].source_created_at == "2026-03-18T08:00:00"
    assert rows[0].source_updated_at == "2026-04-02T09:15:00"
