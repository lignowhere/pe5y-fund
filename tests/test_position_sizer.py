"""Tests for position sizing logic."""
from __future__ import annotations

import sqlite3
import datetime
from pathlib import Path

import pytest

from backend.strategy.position_sizer import (
    CLOSE_SCALE_VND,
    _query_latest_prices,
    portfolio_summary,
    size_portfolio,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a test database with price + volume data."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE stock_price_history (
            symbol TEXT,
            time TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER
        )
    """)

    # Insert price data for test symbols
    prices = {"AAA": 25.0, "BBB": 50.0, "CCC": 100.0}  # in thousands
    volumes = {"AAA": 500000, "BBB": 200000, "CCC": 50000}
    d = datetime.date(2025, 1, 2)
    for _ in range(30):
        if d.weekday() < 5:
            for sym in prices:
                conn.execute(
                    "INSERT INTO stock_price_history VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sym, d.isoformat(), prices[sym], prices[sym] + 1,
                     prices[sym] - 1, prices[sym], volumes[sym]),
                )
        d += datetime.timedelta(days=1)

    conn.commit()
    conn.close()
    return db


class TestQueryLatestPrices:
    def test_returns_all_symbols(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        prices = _query_latest_prices(conn, ["AAA", "BBB", "CCC"])
        conn.close()

        assert len(prices) == 3
        assert prices["AAA"] == pytest.approx(25.0)
        assert prices["BBB"] == pytest.approx(50.0)

    def test_missing_symbol_excluded(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        prices = _query_latest_prices(conn, ["AAA", "ZZZ"])
        conn.close()

        assert "AAA" in prices
        assert "ZZZ" not in prices

    def test_empty_symbols(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        prices = _query_latest_prices(conn, [])
        conn.close()
        assert prices == {}


class TestSizePortfolio:
    def test_basic_sizing(self, db_path: Path):
        capital = 1_000_000_000  # 1B VND
        positions = size_portfolio(db_path, ["AAA", "BBB"], capital)

        assert len(positions) == 2
        for p in positions:
            assert p.current_price_vnd > 0
            assert p.target_shares > 0
            assert p.target_shares % 100 == 0  # lot size
            assert 0 < p.fill_rate <= 1.0

    def test_zero_capital_returns_empty(self, db_path: Path):
        positions = size_portfolio(db_path, ["AAA"], 0)
        assert positions == []

    def test_negative_capital_returns_empty(self, db_path: Path):
        positions = size_portfolio(db_path, ["AAA"], -1000)
        assert positions == []

    def test_no_symbols_returns_empty(self, db_path: Path):
        positions = size_portfolio(db_path, [], 1_000_000_000)
        assert positions == []

    def test_lot_size_rounding(self, db_path: Path):
        positions = size_portfolio(db_path, ["AAA"], 1_000_000_000, lot_size=100)
        for p in positions:
            assert p.target_shares % 100 == 0

    def test_equal_weight(self, db_path: Path):
        """Each stock should get approximately equal allocation."""
        capital = 10_000_000_000  # 10B VND
        positions = size_portfolio(db_path, ["AAA", "BBB", "CCC"], capital)
        per_stock = capital / 3
        for p in positions:
            # Allow some deviation due to lot sizing
            assert abs(p.target_shares * p.current_price_vnd - per_stock) < per_stock * 0.1


    def test_buy_prices_used_for_sizing(self, db_path: Path):
        """When buy_prices provided, shares should be sized at buy price, not current."""
        capital = 1_000_000_000  # 1B VND
        # DB has AAA at 25.0 (thousands) = 25,000 VND current price
        # Set buy price to half: 12,500 VND → should get ~2x more shares
        buy_prices = {"AAA": 12_500.0}
        pos_with_buy = size_portfolio(db_path, ["AAA"], capital, buy_prices=buy_prices)
        pos_without = size_portfolio(db_path, ["AAA"], capital)

        assert len(pos_with_buy) == 1
        assert len(pos_without) == 1

        # With buy price at half, target_shares should be ~2x
        assert pos_with_buy[0].target_shares == pytest.approx(
            pos_without[0].target_shares * 2, rel=0.01,
        )
        # current_price_vnd should still reflect the latest DB price
        assert pos_with_buy[0].current_price_vnd == pos_without[0].current_price_vnd
        # target_value should use buy price (sizing price), not current
        assert pos_with_buy[0].target_value_vnd == pytest.approx(
            pos_with_buy[0].target_shares * 12_500.0, rel=0.01,
        )

    def test_buy_prices_none_fallback(self, db_path: Path):
        """Without buy_prices, sizing should use current price (backward compat)."""
        capital = 1_000_000_000
        pos_none = size_portfolio(db_path, ["AAA"], capital, buy_prices=None)
        pos_default = size_portfolio(db_path, ["AAA"], capital)

        assert pos_none[0].target_shares == pos_default[0].target_shares
        assert pos_none[0].current_price_vnd == pos_default[0].current_price_vnd


class TestPortfolioSummary:
    def test_basic_summary(self, db_path: Path):
        capital = 1_000_000_000
        positions = size_portfolio(db_path, ["AAA", "BBB"], capital)
        summary = portfolio_summary(positions, capital)

        assert summary["stock_count"] == 2
        assert summary["total_deployed_vnd"] > 0
        assert summary["total_deployed_vnd"] <= capital
        assert 0 <= summary["cash_drag_pct"] <= 100
        assert 0 < summary["avg_fill_rate"] <= 1.0

    def test_empty_portfolio(self):
        summary = portfolio_summary([], 1_000_000_000)
        assert summary["stock_count"] == 0
        assert summary["avg_fill_rate"] == 0.0
