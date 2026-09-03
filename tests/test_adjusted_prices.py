"""Adjusted-price cache and total-return input regression tests."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.config import AppConfig, StrategyConfig
from backend.data.db_migration import _create_adjusted_price_cache
from backend.fund.adjusted_prices import ensure_adjusted_performance_prices
from backend.fund.cycle import ActiveCycle
from backend.fund.market_data import vendor_adjusted_price_pairs
from backend.fund.planner import build_strategy_drift_targets
from backend.strategy.signal_pe_ttm_20q import PE20QCandidate


def _cycle() -> ActiveCycle:
    return ActiveCycle(
        formation_year=2024,
        hold_year=2025,
        rebalance_date="2025-09-01",
        snapshot_id=7,
        selected=[
            PE20QCandidate("AAA", 1, 1, 1, 1, quarters_count=20),
            PE20QCandidate("BBB", 1, 1, 1, 2, quarters_count=20),
        ],
        rebalance_prices={
            "AAA": {"price_vnd": 10_000, "price_date": "2025-09-03"},
            "BBB": {"price_vnd": 20_000, "price_date": "2025-09-03"},
        },
    )


class FakeAdjustedClient:
    def __init__(self):
        self.calls: list[str] = []

    def get_ohlcv(self, symbol: str, count_back: int = 60):
        self.calls.append(symbol)
        buy = 8_000 if symbol == "AAA" else 10_000
        current = 12_000 if symbol == "AAA" else 15_000
        return [
            {"time": "2025-09-03", "close": buy},
            {"time": "2026-07-28", "close": current},
        ]


def test_adjusted_prices_are_cached_by_cycle_and_valuation_date(
    tmp_path: Path,
):
    db = tmp_path / "adjusted.db"
    _create_adjusted_price_cache(db)
    config = AppConfig(db_path=db)
    client = FakeAdjustedClient()

    first = ensure_adjusted_performance_prices(
        config,
        _cycle(),
        "2026-07-28",
        client=client,
        max_workers=1,
    )

    assert client.calls == ["AAA", "BBB"]
    assert first["AAA"]["adjusted_rebalance_price_vnd"] == 8_000
    assert first["BBB"]["adjusted_current_price_vnd"] == 15_000

    second_client = FakeAdjustedClient()
    second = ensure_adjusted_performance_prices(
        config,
        _cycle(),
        "2026-07-28",
        client=second_client,
        max_workers=1,
    )
    assert second_client.calls == []
    assert second["AAA"]["source"] == "VCI_GAP_CHART"


def test_legacy_research_uses_execution_open_and_same_vintage_adjustments(
    tmp_path: Path,
):
    db = tmp_path / "legacy-research.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE stock_price_history (
                symbol TEXT NOT NULL, time TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL, volume REAL,
                PRIMARY KEY(symbol, time)
            )"""
        )
        conn.execute(
            """CREATE TABLE market_price_metadata (
                symbol TEXT NOT NULL, price_date TEXT NOT NULL,
                source TEXT, price_basis TEXT, raw_unit TEXT,
                is_provisional INTEGER, observed_at TEXT,
                PRIMARY KEY(symbol, price_date)
            )"""
        )
        conn.execute(
            """CREATE TABLE adjusted_price_history (
                symbol TEXT NOT NULL, price_date TEXT NOT NULL,
                close_vnd REAL NOT NULL, source TEXT NOT NULL,
                price_basis TEXT NOT NULL, source_as_of TEXT NOT NULL,
                fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(symbol, price_date, source_as_of)
            )"""
        )
        conn.executemany(
            """INSERT INTO stock_price_history
               (symbol, time, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                ("AAA", "2025-09-03", 10, 12, 10, 12, 1_000_000),
                ("BBB", "2025-09-03", 20, 20, 20, 20, 1_000_000),
                ("VNINDEX", "2025-09-03", 1000, 1000, 1000, 1000, 0),
                ("AAA", "2026-07-28", 18, 18, 18, 18, 1_000_000),
                ("BBB", "2026-07-28", 10, 10, 10, 10, 1_000_000),
                ("VNINDEX", "2026-07-28", 1200, 1200, 1200, 1200, 0),
            ],
        )
        conn.executemany(
            """INSERT INTO adjusted_price_history
               (symbol, price_date, close_vnd, source, price_basis,
                source_as_of)
               VALUES (?, ?, ?, 'VCI_GAP_CHART',
                       'adjusted_total_return', '2026-07-29')""",
            [
                ("AAA", "2025-09-03", 6_000),
                ("AAA", "2026-07-28", 9_000),
                ("BBB", "2025-09-03", 20_000),
                ("BBB", "2026-07-28", 10_000),
                ("VNINDEX", "2025-09-03", 1_000),
                ("VNINDEX", "2026-07-28", 1_200),
            ],
        )

    config = AppConfig(
        db_path=db,
        strategy=StrategyConfig(
            min_holdings=15,
            lot_size=100,
            participation_rate=0.10,
            accum_days=10,
        ),
    )
    cycle = ActiveCycle(
        formation_year=2024,
        hold_year=2025,
        rebalance_date="2025-09-01",
        execution_date="2025-09-03",
        trust_tier="legacy_research",
        selected=[
            PE20QCandidate("AAA", 1, 1, 1, 1, quarters_count=20),
            PE20QCandidate("BBB", 1, 1, 1, 2, quarters_count=20),
        ],
        rebalance_prices={
            "AAA": {"price_vnd": 10_000, "price_date": "2025-09-03"},
            "BBB": {"price_vnd": 20_000, "price_date": "2025-09-03"},
        },
        adv_shares={"AAA": 1_000_000, "BBB": 1_000_000},
    )

    result = build_strategy_drift_targets(
        config,
        23_000_000,
        cycle,
        valuation_date="2026-07-28",
    )

    assert result["performance_basis"] == (
        "vendor_adjusted_total_return_research"
    )
    assert result["performance_source_as_of"] == "2026-07-29"
    assert result["model_growth_multiple"] == pytest.approx(1.15)
    positions = {row["symbol"]: row for row in result["positions"]}
    assert positions["AAA"]["price_return_pct"] == pytest.approx(80.0)
    assert positions["BBB"]["price_return_pct"] == pytest.approx(-50.0)
    assert positions["AAA"]["drift_weight_pct"] == pytest.approx(
        78.2609, abs=1e-4
    )
    assert positions["AAA"]["corporate_action_share_factor"] is None


def test_adjusted_pairs_choose_one_complete_vintage_for_all_symbols(
    tmp_path: Path,
):
    db = tmp_path / "common-vintage.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE adjusted_price_history (
                symbol TEXT NOT NULL,
                price_date TEXT NOT NULL,
                close_vnd REAL NOT NULL,
                source TEXT NOT NULL,
                price_basis TEXT NOT NULL,
                source_as_of TEXT NOT NULL,
                fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(symbol, price_date, source_as_of)
            )"""
        )
        rows = [
            ("AAA", "2025-09-03", 10, "2026-07-29"),
            ("AAA", "2026-07-28", 12, "2026-07-29"),
            ("BBB", "2025-09-03", 20, "2026-07-29"),
            ("BBB", "2026-07-28", 22, "2026-07-29"),
            # A newer complete vintage exists only for AAA. It must not be
            # mixed with BBB's older vintage.
            ("AAA", "2025-09-03", 11, "2026-07-30"),
            ("AAA", "2026-07-28", 13, "2026-07-30"),
            ("BBB", "2026-07-28", 23, "2026-07-30"),
        ]
        conn.executemany(
            """INSERT INTO adjusted_price_history
               (symbol, price_date, close_vnd, source, price_basis,
                source_as_of)
               VALUES (?, ?, ?, 'VCI_GAP_CHART',
                       'adjusted_total_return', ?)""",
            rows,
        )

    pairs = vendor_adjusted_price_pairs(
        db, ["AAA", "BBB"], "2025-09-03", "2026-07-28"
    )

    assert set(pairs) == {"AAA", "BBB"}
    assert {item["source_as_of"] for item in pairs.values()} == {
        "2026-07-29"
    }
    assert pairs["AAA"]["start_value"] == 10
