"""Tests for durable fund settings and current-holdings trade deltas."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.api import fund_routes
from backend.config import AppConfig
from backend.data.db_migration import _create_fund_tables
from backend.fund.store import (
    get_holdings,
    get_preferences,
    replace_holdings,
    save_preferences,
)


@pytest.fixture
def fund_db(tmp_path: Path) -> Path:
    db = tmp_path / "fund.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE stocks (ticker TEXT PRIMARY KEY)")
        conn.execute(
            """CREATE TABLE stock_price_history (
                symbol TEXT, time TEXT, close REAL, volume INTEGER
            )"""
        )
        conn.executemany(
            "INSERT INTO stocks(ticker) VALUES (?)",
            [("FPT",), ("VCB",), ("HPG",)],
        )
        conn.executemany(
            "INSERT INTO stock_price_history VALUES (?, ?, ?, ?)",
            [
                ("FPT", "2026-07-28", 100.0, 1_000_000),
                ("VCB", "2026-07-28", 60.0, 1_000_000),
                ("HPG", "2026-07-28", 25.0, 1_000_000),
            ],
        )
    _create_fund_tables(db)
    save_preferences(db, "LAST_8Q_PLUS", 10)
    return db


def test_preferences_and_holdings_are_durable(fund_db: Path):
    saved = save_preferences(fund_db, "TTM_20Q", 12)
    assert saved["strategy"] == "TTM_20Q"
    assert saved["select_pct"] == 12
    assert get_preferences(fund_db)["strategy"] == "TTM_20Q"

    replace_holdings(
        fund_db,
        [
            {"symbol": "fpt", "shares": 500},
            {"symbol": "FPT", "shares": 100},
            {"symbol": "HPG", "shares": 200},
        ],
    )
    assert get_holdings(fund_db)["holdings"] == [
        {"symbol": "FPT", "shares": 600},
        {"symbol": "HPG", "shares": 200},
    ]


def test_empty_preferences_restore_last_8q_plus_10_percent(fund_db: Path):
    with sqlite3.connect(fund_db) as conn:
        conn.execute("DELETE FROM fund_preferences")

    assert get_preferences(fund_db) == {
        "strategy": "LAST_8Q_PLUS",
        "select_pct": 10.0,
        "updated_at": None,
    }


def test_unknown_holding_is_rejected(fund_db: Path):
    with pytest.raises(ValueError, match="Mã không tồn tại"):
        replace_holdings(fund_db, [{"symbol": "ZZZ", "shares": 100}])


def test_portfolio_plan_uses_actual_holdings_for_deltas(
    fund_db: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(fund_routes, "_cfg", AppConfig(db_path=fund_db))
    monkeypatch.setattr(
        fund_routes,
        "get_active_snapshot_status",
        lambda *_: {
            "investment_ready": True,
            "blocking_issues": [],
        },
    )
    cycle = type("Cycle", (), {"symbols": ["FPT", "VCB"]})()
    monkeypatch.setattr(
        fund_routes,
        "resolve_active_cycle",
        lambda *_: cycle,
    )
    monkeypatch.setattr(
        fund_routes,
        "build_strategy_drift_targets",
        lambda *_args, **_kwargs: {
            "formation_year": 2024,
            "hold_year": 2025,
            "rebalance_date": "2025-09-01",
            "price_date": "2026-07-28",
            "price_basis": "strategy_date_drift",
            "model_growth_multiple": 1.25,
            "positions": [
                {
                    "symbol": "FPT",
                    "signal_rank": 1,
                    "source": "PRIMARY",
                    "rebalance_price_vnd": 50_000,
                    "rebalance_price_date": "2025-09-03",
                    "current_price_vnd": 100_000,
                    "initial_weight_pct": 50,
                    "drift_weight_pct": 76,
                    "desired_shares": 1_000,
                    "target_shares": 1_000,
                    "adv_shares": 1_000_000,
                    "capacity_shares": 1_000_000,
                    "liquidity_limited": False,
                },
                {
                    "symbol": "VCB",
                    "signal_rank": 2,
                    "source": "PRIMARY",
                    "rebalance_price_vnd": 70_000,
                    "rebalance_price_date": "2025-09-03",
                    "current_price_vnd": 60_000,
                    "initial_weight_pct": 50,
                    "drift_weight_pct": 24,
                    "desired_shares": 500,
                    "target_shares": 500,
                    "adv_shares": 1_000_000,
                    "capacity_shares": 1_000_000,
                    "liquidity_limited": False,
                },
            ],
            "prices": {
                "FPT": {"price_vnd": 100_000, "price_date": "2026-07-28"},
                "VCB": {"price_vnd": 60_000, "price_date": "2026-07-28"},
                "HPG": {"price_vnd": 25_000, "price_date": "2026-07-28"},
            },
            "summary": {
                "target_stock_count": 2,
                "target_deployed_vnd": 130_000_000,
                "target_cash_vnd": 20_000_000,
                "liquidity_limited_count": 0,
            },
        },
    )
    body = fund_routes.PortfolioPlanBody(
        nav_vnd=150_000_000,
        strategy="LAST_8Q_PLUS",
        select_pct=10,
        auto_sync=False,
        holdings=[
            fund_routes.HoldingInput(symbol="FPT", shares=600),
            fund_routes.HoldingInput(symbol="HPG", shares=200),
        ],
    )

    result = fund_routes.create_portfolio_plan(body)
    rows = {row["symbol"]: row for row in result["positions"]}

    assert rows["FPT"]["target_shares"] == 1_000
    assert rows["FPT"]["delta_shares"] == 400
    assert rows["FPT"]["action"] == "MUA"
    assert rows["HPG"]["target_shares"] == 0
    assert rows["HPG"]["delta_shares"] == -200
    assert rows["HPG"]["action"] == "BÁN"
    assert rows["VCB"]["target_shares"] == 500


def test_negative_implied_cash_returns_warning(
    fund_db: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(fund_routes, "_cfg", AppConfig(db_path=fund_db))
    monkeypatch.setattr(
        fund_routes,
        "get_active_snapshot_status",
        lambda *_: {
            "investment_ready": True,
            "blocking_issues": [],
        },
    )
    cycle = type("Cycle", (), {"symbols": []})()
    monkeypatch.setattr(
        fund_routes,
        "resolve_active_cycle",
        lambda *_: cycle,
    )
    monkeypatch.setattr(
        fund_routes,
        "build_strategy_drift_targets",
        lambda *_args, **_kwargs: {
            "formation_year": 2024,
            "hold_year": 2025,
            "rebalance_date": "2025-09-01",
            "price_date": "2026-07-28",
            "price_basis": "strategy_date_drift",
            "model_growth_multiple": 1.0,
            "positions": [],
            "prices": {
                "FPT": {"price_vnd": 100_000, "price_date": "2026-07-28"},
            },
            "summary": {
                "target_stock_count": 0,
                "target_deployed_vnd": 0,
                "target_cash_vnd": 10_000_000,
                "liquidity_limited_count": 0,
            },
        },
    )
    body = fund_routes.PortfolioPlanBody(
        nav_vnd=10_000_000,
        strategy="TTM_20Q",
        select_pct=14,
        auto_sync=False,
        holdings=[fund_routes.HoldingInput(symbol="FPT", shares=600)],
    )
    result = fund_routes.create_portfolio_plan(body)
    assert any(
        warning["code"] == "NEGATIVE_IMPLIED_CASH"
        for warning in result["warnings"]
    )


def test_unverified_snapshot_blocks_plan_without_changing_preference(
    fund_db: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(fund_routes, "_cfg", AppConfig(db_path=fund_db))
    monkeypatch.setattr(
        fund_routes,
        "get_active_snapshot_status",
        lambda *_: {
            "investment_ready": False,
            "blocking_issues": ["LEGACY_SNAPSHOT_UNVERIFIED"],
        },
    )
    before = get_preferences(fund_db)
    body = fund_routes.PortfolioPlanBody(
        nav_vnd=584_000_000,
        strategy="TTM_20Q",
        select_pct=14,
        auto_sync=False,
    )
    with pytest.raises(Exception) as raised:
        fund_routes.create_portfolio_plan(body)
    assert getattr(raised.value, "status_code", None) == 503
    assert raised.value.detail["code"] == "SNAPSHOT_NOT_VERIFIED"
    assert get_preferences(fund_db) == before


def test_legacy_research_planner_requires_opt_in_and_keeps_warning(
    fund_db: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        fund_routes,
        "get_active_snapshot_status",
        lambda *_: {
            "investment_ready": False,
            "research_planner_available": True,
            "blocking_issues": ["LEGACY_SNAPSHOT_UNVERIFIED"],
        },
    )
    body = fund_routes.PortfolioPlanBody(
        nav_vnd=584_000_000,
        strategy="LAST_8Q_PLUS",
        select_pct=10,
        auto_sync=False,
    )
    monkeypatch.setattr(
        fund_routes,
        "_cfg",
        AppConfig(
            db_path=fund_db,
            allow_legacy_research_planner=False,
        ),
    )
    with pytest.raises(Exception) as blocked:
        fund_routes.create_portfolio_plan(body)
    assert getattr(blocked.value, "status_code", None) == 503

    cycle = type(
        "Cycle",
        (),
        {
            "symbols": [],
            "trust_tier": "legacy_research",
            "config_hash_matches": False,
            "strategy_parameters_match": False,
        },
    )()
    monkeypatch.setattr(
        fund_routes,
        "_cfg",
        AppConfig(
            db_path=fund_db,
            allow_legacy_research_planner=True,
        ),
    )
    monkeypatch.setattr(
        fund_routes,
        "resolve_active_cycle",
        lambda *_: cycle,
    )
    monkeypatch.setattr(
        fund_routes,
        "build_strategy_drift_targets",
        lambda *_args, **_kwargs: {
            "formation_year": 2024,
            "hold_year": 2025,
            "rebalance_date": "2025-09-01",
            "execution_date": "2025-09-03",
            "price_date": "2026-07-28",
            "price_basis": "strategy_date_drift",
            "performance_basis": (
                "vendor_adjusted_total_return_research"
            ),
            "trust_tier": "legacy_research",
            "performance_source_as_of": "2026-07-29",
            "model_growth_multiple": 1.0,
            "positions": [],
            "prices": {},
            "summary": {
                "target_stock_count": 0,
                "target_deployed_vnd": 0,
                "target_cash_vnd": 584_000_000,
                "liquidity_limited_count": 0,
            },
        },
    )

    result = fund_routes.create_portfolio_plan(body)
    assert result["trust_tier"] == "legacy_research"
    assert {
        warning["code"] for warning in result["warnings"]
    } >= {
        "LEGACY_RESEARCH_DATA",
        "VENDOR_ADJUSTED_PERFORMANCE",
        "LEGACY_CONFIG_HASH_MISMATCH",
    }


def test_owner_confirmed_local_snapshot_unlocks_with_truthful_label(
    fund_db: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        fund_routes,
        "_cfg",
        AppConfig(db_path=fund_db),
    )
    monkeypatch.setattr(
        fund_routes,
        "get_active_snapshot_status",
        lambda *_: {
            "investment_ready": False,
            "user_confirmed_ready": True,
            "blocking_issues": [],
        },
    )
    cycle = type(
        "Cycle",
        (),
        {
            "symbols": [],
            "trust_tier": "trusted_local",
            "config_hash_matches": True,
            "strategy_parameters_match": True,
        },
    )()
    monkeypatch.setattr(
        fund_routes,
        "resolve_active_cycle",
        lambda *_: cycle,
    )
    monkeypatch.setattr(
        fund_routes,
        "build_strategy_drift_targets",
        lambda *_args, **_kwargs: {
            "formation_year": 2024,
            "hold_year": 2025,
            "rebalance_date": "2025-09-01",
            "execution_date": "2025-09-03",
            "price_date": "2026-07-28",
            "price_basis": "strategy_date_drift",
            "performance_basis": (
                "vendor_adjusted_total_return_user_confirmed"
            ),
            "trust_tier": "trusted_local",
            "performance_source_as_of": "2026-07-29",
            "model_growth_multiple": 1.0,
            "positions": [],
            "prices": {},
            "summary": {
                "target_stock_count": 0,
                "target_deployed_vnd": 0,
                "target_cash_vnd": 584_000_000,
                "liquidity_limited_count": 0,
            },
        },
    )
    body = fund_routes.PortfolioPlanBody(
        nav_vnd=584_000_000,
        strategy="LAST_8Q_PLUS",
        select_pct=10,
        auto_sync=False,
    )

    result = fund_routes.create_portfolio_plan(body)

    assert result["trust_tier"] == "trusted_local"
    assert result["performance_basis"] == (
        "vendor_adjusted_total_return_user_confirmed"
    )
    assert {
        warning["code"] for warning in result["warnings"]
    } == {
        "USER_CONFIRMED_LOCAL_DATA",
        "VENDOR_ADJUSTED_PERFORMANCE",
    }
