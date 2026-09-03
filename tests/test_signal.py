"""Tests for PE_TTM_20Q signal generation logic."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from backend.config import AppConfig
from backend.strategy.signal_pe_ttm_20q import (
    CLOSE_SCALE_VND,
    PE20QCandidate,
    _latest_quarter,
    _query_latest_close,
    _query_market_cap,
    _query_quarterly_eps,
    generate_signal_20q,
    select_top_n_20q,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a minimal in-memory test database."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE stocks (
            ticker TEXT PRIMARY KEY,
            organ_name TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE stock_exchange (
            ticker TEXT PRIMARY KEY,
            exchange TEXT
        )
    """)
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
    conn.execute("""
        CREATE TABLE financial_ratios (
            symbol TEXT,
            year INTEGER,
            quarter INTEGER,
            eps_vnd REAL,
            market_cap_billions REAL
        )
    """)

    # Insert test stocks
    for sym in ["AAA", "BBB", "CCC", "DDD"]:
        conn.execute("INSERT INTO stocks VALUES (?, ?)", (sym, f"Company {sym}"))
        conn.execute("INSERT INTO stock_exchange VALUES (?, 'HSX')", (sym,))

    # Insert 20 quarters of EPS for each stock (2020Q1 - 2024Q4)
    for sym_idx, sym in enumerate(["AAA", "BBB", "CCC", "DDD"]):
        eps_base = (sym_idx + 1) * 500  # AAA=500, BBB=1000, CCC=1500, DDD=2000
        for year in range(2020, 2025):
            for q in range(1, 5):
                conn.execute(
                    "INSERT INTO financial_ratios (symbol, year, quarter, eps_vnd) VALUES (?, ?, ?, ?)",
                    (sym, year, q, eps_base + (year - 2020) * 50 + q * 10),
                )
            # Annual market cap
            mcap = 500_000_000_000 + sym_idx * 200_000_000_000  # 500B - 1.1T VND
            conn.execute(
                "INSERT INTO financial_ratios (symbol, year, quarter, market_cap_billions) VALUES (?, ?, NULL, ?)",
                (sym, year, mcap),
            )

    # Insert price history (full year 2024 + some 2025) with varying prices
    import datetime
    import random
    rng = random.Random(42)  # deterministic
    for sym_idx, sym in enumerate(["AAA", "BBB", "CCC", "DDD"]):
        base_price = 20.0 + sym_idx * 10  # 20k, 30k, 40k, 50k (in thousands)
        d = datetime.date(2024, 1, 2)
        count = 0
        while d <= datetime.date(2025, 9, 15) and count < 300:
            if d.weekday() < 5:  # skip weekends
                # Add random variation to avoid 100% stale close filter
                noise = rng.uniform(-0.5, 0.5)
                price = base_price + noise
                conn.execute(
                    "INSERT INTO stock_price_history VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sym, d.isoformat(), price, price + 1, price - 1, price, 100000 + sym_idx * 50000),
                )
                count += 1
            d += datetime.timedelta(days=1)

    conn.commit()
    conn.close()
    return db


class TestLatestQuarter:
    def test_january_returns_prev_q3(self):
        assert _latest_quarter(2025, 1) == (2024, 3)

    def test_march_returns_prev_q4(self):
        assert _latest_quarter(2025, 3) == (2024, 4)

    def test_may_returns_current_q1(self):
        assert _latest_quarter(2025, 5) == (2025, 1)

    def test_september_returns_current_q2(self):
        assert _latest_quarter(2025, 9) == (2025, 2)

    def test_november_returns_current_q3(self):
        assert _latest_quarter(2025, 11) == (2025, 3)


class TestQueryLatestClose:
    def test_returns_deterministic_price(self, db_path: Path):
        """The fixed SQL should always return the same price for same data."""
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        prices = _query_latest_close(conn, 2024)
        conn.close()

        assert "AAA" in prices
        assert "BBB" in prices
        assert prices["AAA"] > 0
        # Run again — should be identical (deterministic)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        prices2 = _query_latest_close(conn, 2024)
        conn.close()
        assert prices == prices2

    def test_no_data_returns_empty(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        prices = _query_latest_close(conn, 2010)  # no data for 2010
        conn.close()
        assert prices == {}


class TestQueryQuarterlyEps:
    def test_returns_20_quarters(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        result = _query_quarterly_eps(conn, 2024, 4, require_all_positive=False)
        conn.close()

        assert len(result) == 4  # AAA, BBB, CCC, DDD
        for r in result:
            assert r["cnt"] == 20
            assert r["avg_eps"] > 0

    def test_supports_shorter_complete_window(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        result = _query_quarterly_eps(
            conn,
            2024,
            4,
            quarter_count=8,
            require_all_positive=False,
        )
        conn.close()

        assert len(result) == 4
        assert {row["cnt"] for row in result} == {8}

    def test_rejects_incomplete_requested_window(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """DELETE FROM financial_ratios
               WHERE symbol = 'AAA' AND year = 2024 AND quarter = 1"""
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        result = _query_quarterly_eps(
            conn,
            2024,
            4,
            quarter_count=8,
            require_all_positive=False,
        )
        conn.close()

        assert "AAA" not in {row["symbol"] for row in result}

    def test_default_window_remains_20_quarters(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        result = _query_quarterly_eps(
            conn, 2024, 4, require_all_positive=False
        )
        conn.close()

        assert {row["cnt"] for row in result} == {20}

    def test_relaxed_allows_avg_positive(self, db_path: Path):
        # Add a stock with some negative EPS but avg > 0
        conn = sqlite3.connect(str(db_path))
        conn.execute("INSERT INTO stocks VALUES ('NEG', 'Negative Co')")
        for year in range(2020, 2025):
            for q in range(1, 5):
                eps = -100 if (year == 2020 and q <= 2) else 500
                conn.execute(
                    "INSERT INTO financial_ratios (symbol, year, quarter, eps_vnd) VALUES (?, ?, ?, ?)",
                    ("NEG", year, q, eps),
                )
        conn.commit()

        conn.row_factory = sqlite3.Row
        # Strict mode should exclude NEG
        strict = _query_quarterly_eps(conn, 2024, 4, require_all_positive=True)
        neg_strict = [r for r in strict if r["symbol"] == "NEG"]
        assert len(neg_strict) == 0

        # Relaxed mode should include NEG (avg > 0)
        relaxed = _query_quarterly_eps(conn, 2024, 4, require_all_positive=False)
        neg_relaxed = [r for r in relaxed if r["symbol"] == "NEG"]
        assert len(neg_relaxed) == 1
        conn.close()

    def test_last8_gate_keeps_twenty_quarter_valuation_window(
        self, db_path: Path
    ):
        """LAST8 is a 20Q valuation plus an eight-profitable-quarters gate."""
        periods = [
            (year, quarter)
            for year in range(2020, 2025)
            for quarter in range(1, 5)
        ]
        with sqlite3.connect(str(db_path)) as conn:
            for index, (year, quarter) in enumerate(periods):
                # Old losses remain part of the 20Q average; the latest eight
                # quarters are profitable.
                eps = -100 if index < 12 else 500
                conn.execute(
                    """INSERT INTO financial_ratios
                       (symbol, year, quarter, eps_vnd)
                       VALUES ('OLD', ?, ?, ?)""",
                    (year, quarter, eps),
                )
                # A recent loss must fail the LAST8 gate.
                recent_eps = -1 if index == 19 else 500
                conn.execute(
                    """INSERT INTO financial_ratios
                       (symbol, year, quarter, eps_vnd)
                       VALUES ('REC', ?, ?, ?)""",
                    (year, quarter, recent_eps),
                )
            conn.row_factory = sqlite3.Row
            result = _query_quarterly_eps(
                conn,
                2024,
                4,
                quarter_count=20,
                require_all_positive=False,
                require_last_n_positive=8,
            )

        by_symbol = {row["symbol"]: row for row in result}
        assert by_symbol["OLD"]["cnt"] == 20
        assert by_symbol["OLD"]["avg_eps"] == 140
        assert "REC" not in by_symbol

    def test_point_in_time_query_excludes_future_revision(
        self, tmp_path: Path
    ):
        db = tmp_path / "pit.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                """CREATE TABLE financial_ratio_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    financial_data_version_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    quarter INTEGER,
                    eps_vnd REAL,
                    market_cap_billions REAL,
                    available_at TEXT,
                    publication_status TEXT NOT NULL
                )"""
            )
            periods = [
                (year, quarter)
                for year in range(2020, 2025)
                for quarter in range(1, 5)
            ]
            conn.executemany(
                """INSERT INTO financial_ratio_versions
                   (financial_data_version_id, symbol, year, quarter,
                    eps_vnd, available_at, publication_status)
                   VALUES (1, 'AAA', ?, ?, 100, '2025-01-01', 'verified')""",
                periods,
            )
            conn.execute(
                """INSERT INTO financial_ratio_versions
                   (financial_data_version_id, symbol, year, quarter,
                    eps_vnd, available_at, publication_status)
                   VALUES (2, 'AAA', 2024, 4, 900,
                           '2025-02-01', 'verified')"""
            )
            conn.executemany(
                """INSERT INTO financial_ratio_versions
                   (financial_data_version_id, symbol, year, quarter,
                    market_cap_billions, available_at, publication_status)
                   VALUES (?, 'AAA', 2024, NULL, ?, ?, 'verified')""",
                [
                    (1, 500_000_000_000, "2025-01-01"),
                    (2, 900_000_000_000, "2025-02-01"),
                ],
            )
            conn.row_factory = sqlite3.Row
            before = _query_quarterly_eps(
                conn,
                2024,
                4,
                require_all_positive=False,
                as_of_date="2025-01-15",
                financial_data_version_id=2,
            )
            after = _query_quarterly_eps(
                conn,
                2024,
                4,
                require_all_positive=False,
                as_of_date="2025-02-15",
                financial_data_version_id=2,
            )
            mcap_before = _query_market_cap(
                conn,
                2024,
                as_of_date="2025-01-15",
                financial_data_version_id=2,
            )
            mcap_after = _query_market_cap(
                conn,
                2024,
                as_of_date="2025-02-15",
                financial_data_version_id=2,
            )

        assert before[0]["avg_eps"] == 100
        assert after[0]["avg_eps"] == 140
        assert mcap_before["AAA"] == 500_000_000_000
        assert mcap_after["AAA"] == 900_000_000_000


class TestSelectTopN:
    def test_basic_selection(self):
        candidates = [
            PE20QCandidate("A", 100, 5.0, 1e12, 1),
            PE20QCandidate("B", 200, 6.0, 1e12, 2),
            PE20QCandidate("C", 300, 7.0, 1e12, 3),
            PE20QCandidate("D", 400, 8.0, 1e12, 4),
            PE20QCandidate("E", 500, 9.0, 1e12, 5),
            PE20QCandidate("F", 600, 10.0, 1e12, 6),
            PE20QCandidate("G", 700, 11.0, 1e12, 7),
            PE20QCandidate("H", 800, 12.0, 1e12, 8),
            PE20QCandidate("I", 900, 13.0, 1e12, 9),
            PE20QCandidate("J", 1000, 14.0, 1e12, 10),
        ]
        # The system floor is 15, but only 10 candidates are available.
        selected = select_top_n_20q(candidates, 14.0, min_holdings=2)
        assert len(selected) == 10

    def test_empty_candidates(self):
        assert select_top_n_20q([], 14.0) == []

    def test_min_holdings_floor(self):
        candidates = [PE20QCandidate(f"S{i}", 100, float(i), 1e12, i) for i in range(1, 100)]
        selected = select_top_n_20q(candidates, 5.0, min_holdings=10)
        assert len(selected) == 15

    def test_default_selects_at_least_fifteen(self):
        candidates = [
            PE20QCandidate(f"S{i}", 100, float(i), 1e12, i)
            for i in range(1, 21)
        ]
        selected = select_top_n_20q(candidates, 10.0)
        assert len(selected) == 15


def _relaxed_config(db_path: Path) -> AppConfig:
    """Config with relaxed liquidity filters for test fixtures."""
    from backend.config import StrategyConfig
    sc = StrategyConfig(
        min_trading_days=10,
        min_avg_dollar_volume_vnd=0,
        mcap_base_vnd=0,
    )
    return AppConfig(db_path=db_path, strategy=sc)


class TestGenerateSignal:
    def test_generates_ranked_candidates(self, db_path: Path):
        config = _relaxed_config(db_path)
        candidates = generate_signal_20q(
            db_path, 2024, config,
            rebalance_month=9,
            require_all_positive=False,
        )
        assert len(candidates) > 0
        # Should be sorted by PE ascending
        for i in range(1, len(candidates)):
            assert candidates[i].pe_ttm_20q >= candidates[i - 1].pe_ttm_20q
        # Ranks should be sequential
        for i, c in enumerate(candidates):
            assert c.signal_rank == i + 1

    def test_price_in_vnd(self, db_path: Path):
        config = _relaxed_config(db_path)
        candidates = generate_signal_20q(
            db_path, 2024, config,
            rebalance_month=9,
            require_all_positive=False,
        )
        assert len(candidates) > 0
        for c in candidates:
            assert c.buy_price_vnd is not None
            # Price should be in VND (thousands scale * 1000)
            assert c.buy_price_vnd >= 1000, f"{c.symbol} price {c.buy_price_vnd} < 1000 VND"
