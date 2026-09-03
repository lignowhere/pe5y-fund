"""Tests for strategy-date share ratios scaled to current NAV."""
from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from backend.config import AppConfig
from backend.fund.planner import (
    ActiveCycle,
    PlannerDataError,
    build_strategy_drift_targets,
)
from backend.strategy.signal_pe_ttm_20q import PE20QCandidate


@pytest.fixture
def drift_db(tmp_path: Path) -> Path:
    db = tmp_path / "drift.db"
    rows: list[tuple[str, str, float, int]] = []
    for offset in range(20):
        date = (dt.date(2025, 8, 1) + dt.timedelta(days=offset)).isoformat()
        rows.extend([
            ("AAA", date, 10.0, 1_000_000),
            ("BBB", date, 20.0, 1_000_000),
        ])
    rows.extend([
        ("AAA", "2025-09-01", 10.0, 1_000_000),
        ("BBB", "2025-09-01", 20.0, 1_000_000),
        ("VNINDEX", "2025-09-01", 1_000.0, 0),
        ("AAA", "2026-07-28", 20.0, 1_000_000),
        ("BBB", "2026-07-28", 10.0, 1_000_000),
        ("VNINDEX", "2026-07-28", 1_100.0, 0),
    ])
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE stock_price_history (
                symbol TEXT, time TEXT, close REAL, volume INTEGER
            )"""
        )
        conn.execute(
            """CREATE TABLE corporate_action_coverage (
                symbol TEXT, start_date TEXT, end_date TEXT,
                coverage_status TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE corporate_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, action_type TEXT, ex_date TEXT,
                payment_date TEXT, cash_vnd_per_share REAL,
                share_factor REAL, verification_status TEXT
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
            "INSERT INTO stock_price_history VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.executemany(
            """INSERT INTO corporate_action_coverage
               VALUES (?, '2025-09-01', '2026-07-28', 'verified')""",
            [("AAA",), ("BBB",)],
        )
        conn.executemany(
            """INSERT INTO market_price_metadata
               (symbol, price_date, source, price_basis, raw_unit,
                is_provisional, observed_at, source_url,
                source_payload_sha256)
               VALUES (?, '2026-07-28', 'TEST', 'current_spot',
                       'THOUSAND_VND', 0, '2026-07-28T10:00:00Z',
                       'https://example.test/prices', ?)""",
            [("AAA", "a" * 64), ("BBB", "b" * 64)],
        )
        conn.executemany(
            """INSERT INTO price_source_observations
               VALUES (?, '2026-07-28', 'TEST', ?, 1, 'verified')""",
            [("AAA", "a" * 64), ("BBB", "b" * 64)],
        )
    return db


def _cycle() -> ActiveCycle:
    return ActiveCycle(
        formation_year=2024,
        hold_year=2025,
        rebalance_date="2025-09-01",
        selected=[
            PE20QCandidate(
                symbol="AAA",
                avg_eps_20q=1_000,
                pe_ttm_20q=10,
                market_cap_vnd=1,
                signal_rank=1,
                buy_price_vnd=10_000,
                quarters_count=20,
            ),
            PE20QCandidate(
                symbol="BBB",
                avg_eps_20q=1_000,
                pe_ttm_20q=20,
                market_cap_vnd=1,
                signal_rank=2,
                buy_price_vnd=20_000,
                quarters_count=20,
            ),
        ],
    )


def test_weights_drift_without_watch_skip_filter(drift_db: Path):
    result = build_strategy_drift_targets(
        AppConfig(db_path=drift_db),
        100_000_000,
        _cycle(),
        valuation_date="2026-07-28",
    )
    rows = {row["symbol"]: row for row in result["positions"]}

    assert rows["AAA"]["initial_weight_pct"] == 50
    assert rows["BBB"]["initial_weight_pct"] == 50
    assert rows["AAA"]["drift_weight_pct"] == 80
    assert rows["BBB"]["drift_weight_pct"] == 20
    assert rows["AAA"]["target_shares"] == 4_000
    assert rows["BBB"]["target_shares"] == 2_000
    # AAA doubled after rebalance but remains held instead of becoming SKIP.
    assert rows["AAA"]["target_shares"] > 0
    assert rows["AAA"]["target_shares"] / rows["BBB"]["target_shares"] == 2
    assert rows["AAA"]["price_return_pct"] == 100
    assert rows["BBB"]["price_return_pct"] == -50
    assert result["summary"]["strategy_price_return_pct"] == 25
    assert result["summary"]["model_value_per_100m_vnd"] == 125_000_000
    assert result["summary"]["benchmark_symbol"] == "VNINDEX"
    assert result["summary"]["benchmark_return_pct"] == 10
    assert result["summary"]["benchmark_value_per_100m_vnd"] == 110_000_000
    assert result["summary"]["excess_return_pct"] == 15
    assert result["summary"]["gainers_count"] == 1
    assert result["summary"]["losers_count"] == 1
    assert result["summary"]["unchanged_count"] == 0
    assert result["price_basis"] == "strategy_date_drift"
    assert result["summary"]["target_cash_vnd"] == 0


def test_historical_adv_caps_shares_and_leaves_cash(drift_db: Path):
    with sqlite3.connect(drift_db) as conn:
        conn.execute(
            """UPDATE stock_price_history SET volume = 100
               WHERE symbol = 'AAA' AND time < '2025-09-01'"""
        )

    result = build_strategy_drift_targets(
        AppConfig(db_path=drift_db),
        100_000_000,
        _cycle(),
        valuation_date="2026-07-28",
    )
    rows = {row["symbol"]: row for row in result["positions"]}

    assert rows["AAA"]["desired_shares"] == 4_000
    assert rows["AAA"]["target_shares"] == 100
    assert rows["AAA"]["liquidity_limited"] is True
    assert result["summary"]["liquidity_limited_count"] == 1
    assert result["summary"]["target_cash_vnd"] > 0


def test_verified_actions_drive_total_return_and_cash(drift_db: Path):
    with sqlite3.connect(drift_db) as conn:
        conn.executemany(
            """INSERT INTO corporate_actions
               (symbol, action_type, ex_date, payment_date,
                cash_vnd_per_share, share_factor, verification_status)
               VALUES (?, ?, ?, ?, ?, ?, 'verified')""",
            [
                (
                    "AAA",
                    "cash_dividend",
                    "2026-01-05",
                    "2026-02-05",
                    2_000,
                    None,
                ),
                (
                    "BBB",
                    "stock_dividend",
                    "2026-01-05",
                    None,
                    None,
                    2.0,
                ),
            ],
        )
    result = build_strategy_drift_targets(
        AppConfig(db_path=drift_db),
        100_000_000,
        _cycle(),
        valuation_date="2026-07-28",
        adjusted_prices={
            "AAA": {
                "adjusted_rebalance_price_vnd": 8_000,
                "adjusted_current_price_vnd": 20_000,
            },
            "BBB": {
                "adjusted_rebalance_price_vnd": 10_000,
                "adjusted_current_price_vnd": 10_000,
            },
        },
    )
    rows = {row["symbol"]: row for row in result["positions"]}

    assert result["performance_basis"] == "verified_corporate_action_ledger_v1"
    assert result["summary"]["strategy_price_return_pct"] == 60
    assert result["summary"]["model_cash_weight_pct"] == 6.25
    assert rows["AAA"]["price_return_pct"] == 120
    assert rows["BBB"]["price_return_pct"] == 0
    assert rows["AAA"]["drift_weight_pct"] == 62.5
    assert rows["BBB"]["drift_weight_pct"] == 31.25
    assert rows["BBB"]["corporate_action_share_factor"] == 2


def test_missing_common_valuation_price_blocks_plan(drift_db: Path):
    with pytest.raises(PlannerDataError, match="thiếu giá ngày 2026-07-29"):
        build_strategy_drift_targets(
            AppConfig(db_path=drift_db),
            100_000_000,
            _cycle(),
            valuation_date="2026-07-29",
        )


def test_snapshot_market_inputs_are_not_recalculated(drift_db: Path):
    cycle = replace(
        _cycle(),
        snapshot_id=7,
        rebalance_prices={
            "AAA": {"price_vnd": 10_000, "price_date": "2025-09-01"},
            "BBB": {"price_vnd": 20_000, "price_date": "2025-09-01"},
        },
        adv_shares={"AAA": 1_000_000, "BBB": 1_000_000},
        canonical_price_source="TEST",
    )
    with sqlite3.connect(drift_db) as conn:
        conn.execute(
            """UPDATE stock_price_history SET close = 99
               WHERE time = '2025-09-01'"""
        )

    result = build_strategy_drift_targets(
        AppConfig(db_path=drift_db),
        100_000_000,
        cycle,
        valuation_date="2026-07-28",
    )
    rows = {row["symbol"]: row for row in result["positions"]}
    assert rows["AAA"]["rebalance_price_vnd"] == 10_000
    assert rows["BBB"]["rebalance_price_vnd"] == 20_000
    assert result["snapshot_id"] == 7
