"""Tests for configuration module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.config import (
    AppConfig,
    StrategyConfig,
    get_config,
    reload_config,
)


class TestStrategyConfig:
    def test_defaults(self):
        sc = StrategyConfig()
        assert sc.rebalance_month == 9
        assert sc.min_holdings == 15
        assert sc.lot_size == 100
        assert sc.participation_rate == 0.10
        assert sc.transaction_cost_bps == 40.0
        assert sc.broker_fee_bps == 15.0
        assert sc.sell_tax_bps == 10.0

    def test_frozen(self):
        sc = StrategyConfig()
        with pytest.raises(AttributeError):
            sc.lot_size = 200  # type: ignore

    def test_transaction_cost_breakdown(self):
        """Total cost should equal broker fees + sell tax."""
        sc = StrategyConfig()
        expected_round_trip = sc.broker_fee_bps * 2 + sc.sell_tax_bps
        assert sc.transaction_cost_bps == expected_round_trip


class TestAppConfig:
    def test_defaults(self):
        cfg = AppConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8002
        assert isinstance(cfg.strategy, StrategyConfig)

    def test_get_config_cached(self):
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2  # same object (cached)

    def test_reload_config(self):
        old = get_config()
        new = reload_config()
        # After reload, should be a fresh build
        assert isinstance(new, AppConfig)


class TestMarketCapFilter:
    def test_base_year(self):
        from backend.strategy.market_cap_filter import get_min_market_cap
        mcap = get_min_market_cap(2015)
        assert mcap == 200_000_000_000  # 200B VND

    def test_growth(self):
        from backend.strategy.market_cap_filter import get_min_market_cap
        mcap_2017 = get_min_market_cap(2017)
        assert mcap_2017 > 200_000_000_000  # should be 220B

    def test_before_base_year(self):
        from backend.strategy.market_cap_filter import get_min_market_cap
        mcap = get_min_market_cap(2010)
        assert mcap == 200_000_000_000  # returns base
