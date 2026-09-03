from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.api.config_routes import StrategyConfigBody
from backend.data.updater import _insert_bars
from backend.fund.snapshots import (
    StrategySnapshotError,
    _activate_snapshot_and_config,
)
from backend.strategy.position_sizer import query_latest_price_date
from backend.utils.backup import (
    backup_database,
    ensure_daily_evidence_backup,
    verify_backup,
    verify_evidence_backup,
)


def _price_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE stock_price_history (
            symbol TEXT,
            time TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            UNIQUE(symbol, time)
        )"""
    )
    conn.execute(
        """CREATE TABLE market_price_metadata (
            symbol TEXT NOT NULL,
            price_date TEXT NOT NULL,
            source TEXT NOT NULL,
            price_basis TEXT NOT NULL,
            raw_unit TEXT NOT NULL,
            is_provisional INTEGER NOT NULL,
            observed_at TEXT NOT NULL,
            source_url TEXT,
            source_payload_sha256 TEXT,
            PRIMARY KEY(symbol, price_date)
        )"""
    )


def test_completed_price_upsert_replaces_intraday_without_value_heuristic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    db = tmp_path / "prices.db"
    with sqlite3.connect(db) as conn:
        _price_schema(conn)
    monkeypatch.setattr(
        "backend.data.updater._session_is_provisional",
        lambda _date: True,
    )
    first = [{
        "time": "2026-07-29",
        "open": 590.0,
        "high": 605.0,
        "low": 585.0,
        "close": 600.0,
        "volume": 1_000,
    }]
    assert _insert_bars(db, "VNZ", first, source="VCI", price_basis="current_spot") == 1

    monkeypatch.setattr(
        "backend.data.updater._session_is_provisional",
        lambda _date: False,
    )
    final = [{**first[0], "close": 610.0, "high": 615.0, "volume": 2_000}]
    assert _insert_bars(db, "VNZ", final, source="VCI", price_basis="current_spot") == 1

    with sqlite3.connect(db) as conn:
        close, volume = conn.execute(
            """SELECT close, volume FROM stock_price_history
               WHERE symbol = 'VNZ' AND time = '2026-07-29'"""
        ).fetchone()
        provisional = conn.execute(
            """SELECT is_provisional FROM market_price_metadata
               WHERE symbol = 'VNZ' AND price_date = '2026-07-29'"""
        ).fetchone()[0]
    assert close == 610.0
    assert volume == 2_000
    assert provisional == 0


def test_broad_market_date_excludes_provisional_rows(tmp_path: Path):
    db = tmp_path / "sessions.db"
    with sqlite3.connect(db) as conn:
        _price_schema(conn)
        for index in range(100):
            symbol = f"S{index:03d}"
            conn.execute(
                """INSERT INTO stock_price_history
                   VALUES (?, '2026-07-28', 10, 10, 10, 10, 1000)""",
                (symbol,),
            )
            conn.execute(
                """INSERT INTO market_price_metadata
                   (symbol, price_date, source, price_basis, raw_unit,
                    is_provisional, observed_at, source_url,
                    source_payload_sha256)
                   VALUES (?, '2026-07-28', 'VCI', 'current_spot',
                           'THOUSAND_VND', 0, '2026-07-28T10:00:00Z',
                           'https://example.test/prices', ?)""",
                (symbol, "b" * 64),
            )
            conn.execute(
                """INSERT INTO stock_price_history
                   VALUES (?, '2026-07-29', 11, 11, 11, 11, 1000)""",
                (symbol,),
            )
            conn.execute(
                """INSERT INTO market_price_metadata
                   (symbol, price_date, source, price_basis, raw_unit,
                    is_provisional, observed_at, source_url,
                    source_payload_sha256)
                   VALUES (?, '2026-07-29', 'VCI', 'current_spot',
                           'THOUSAND_VND', 1, '2026-07-29T03:00:00Z',
                           'https://example.test/prices', ?)""",
                (symbol, "a" * 64),
            )
    assert query_latest_price_date(db) == "2026-07-28"


@pytest.mark.parametrize(
    "payload",
    [
        {"rebalance_month": 13},
        {"participation_rate": -1},
        {"lot_size": 0},
        {"select_pcts": [-10, 999]},
        {"benchmark_symbol": "vn index"},
    ],
)
def test_strategy_config_rejects_unsafe_values(payload: dict):
    with pytest.raises(ValidationError):
        StrategyConfigBody.model_validate(payload)


def test_backup_has_checksum_and_passes_restore_validation(tmp_path: Path):
    db = tmp_path / "fund.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO sample(value) VALUES ('ok')")
    backup = backup_database(db, tmp_path / "backups", max_backups=2)
    result = verify_backup(backup)
    assert result["quick_check"] == "ok"
    assert backup.with_suffix(".db.sha256").exists()


def test_evidence_backup_has_per_file_checksums(tmp_path: Path):
    evidence = tmp_path / "provenance_documents"
    evidence.mkdir()
    (evidence / "filing.pdf").write_bytes(b"official filing fixture")
    backup = ensure_daily_evidence_backup(
        evidence, tmp_path / "backups", max_backups=2
    )
    result = verify_evidence_backup(backup)
    assert result["file_count"] == 1
    assert (backup / "files" / "filing.pdf").read_bytes() == (
        b"official filing fixture"
    )


def test_snapshot_and_pending_config_activate_atomically(tmp_path: Path):
    db = tmp_path / "atomic-activation.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE strategy_snapshot_sets (
                id INTEGER PRIMARY KEY,
                is_active INTEGER NOT NULL,
                activated_at TEXT,
                lifecycle_status TEXT NOT NULL,
                portfolio_ready INTEGER NOT NULL,
                validated_at TEXT
            );
            CREATE TABLE strategy_config_versions (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                activated_at TEXT,
                error TEXT
            );
            INSERT INTO strategy_snapshot_sets
            VALUES (1, 1, 'old', 'active', 1, 'old');
            INSERT INTO strategy_snapshot_sets
            VALUES (2, 0, NULL, 'building', 1, NULL);
            INSERT INTO strategy_config_versions VALUES (10, 'active', 'old', NULL);
            INSERT INTO strategy_config_versions VALUES (11, 'pending', NULL, NULL);
            """
        )
        _activate_snapshot_and_config(
            conn, 2, pending_config_version_id=11
        )
        conn.commit()
        assert conn.execute(
            "SELECT id FROM strategy_snapshot_sets WHERE is_active = 1"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT id FROM strategy_config_versions WHERE status = 'active'"
        ).fetchone()[0] == 11


def test_failed_atomic_activation_keeps_previous_snapshot_and_config(
    tmp_path: Path,
):
    db = tmp_path / "failed-activation.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE strategy_snapshot_sets (
                id INTEGER PRIMARY KEY,
                is_active INTEGER NOT NULL,
                activated_at TEXT,
                lifecycle_status TEXT NOT NULL,
                portfolio_ready INTEGER NOT NULL,
                validated_at TEXT
            );
            CREATE TABLE strategy_config_versions (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                activated_at TEXT,
                error TEXT
            );
            INSERT INTO strategy_snapshot_sets
            VALUES (1, 1, 'old', 'active', 1, 'old');
            INSERT INTO strategy_snapshot_sets
            VALUES (2, 0, NULL, 'building', 1, NULL);
            INSERT INTO strategy_config_versions VALUES (10, 'active', 'old', NULL);
            """
        )
        with pytest.raises(StrategySnapshotError):
            _activate_snapshot_and_config(
                conn, 2, pending_config_version_id=999
            )
        conn.rollback()
        assert conn.execute(
            "SELECT id FROM strategy_snapshot_sets WHERE is_active = 1"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT id FROM strategy_config_versions WHERE status = 'active'"
        ).fetchone()[0] == 10
