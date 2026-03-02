"""Central configuration for PE5Y Fund System."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

# Load .env from project root
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


@dataclass(frozen=True)
class StrategyConfig:
    rebalance_month: int = 9
    select_pcts: list[float] = field(default_factory=lambda: [10.0, 12.0, 14.0, 16.0])
    participation_rate: float = 0.10
    accum_days: int = 10
    lot_size: int = 100
    # Market cap filter: base 200B VND, +10% every 2 years from 2015
    mcap_base_vnd: float = 200_000_000_000.0
    mcap_growth_rate: float = 0.10
    mcap_growth_period_years: int = 2
    mcap_base_year: int = 2015
    # Liquidity
    min_trading_days: int = 120
    min_avg_dollar_volume_vnd: float = 200_000_000.0
    max_zero_volume_frac: float = 0.05
    max_stale_close_frac: float = 0.20
    max_rebalance_gap_days: int = 10
    # Costs
    transaction_cost_bps: float = 10.0
    # Price scale in DB
    close_scale_vnd: float = 1000.0


@dataclass(frozen=True)
class VCIConfig:
    graphql_url: str = "https://trading.vietcap.com.vn/data-mt/graphql"
    ohlc_url: str = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
    rate_limit_rpm: int = 30
    cache_ttl_financial_days: int = 30
    cache_ttl_price_days: int = 1


@dataclass(frozen=True)
class KBSConfig:
    base_url: str = "https://kbbuddywts.kbsec.com.vn/iis-server/investment"
    price_url: str = "https://kbbuddywts.kbsec.com.vn/sas/kbsv-stock-data-store"
    rate_limit_rpm: int = 30
    cache_ttl_hours: int = 24


@dataclass(frozen=True)
class AppConfig:
    db_path: Path = field(default_factory=lambda: Path(
        os.environ.get("PE5Y_DB_PATH", "vietnam_stocks.db")
    ))
    host: str = "127.0.0.1"
    port: int = 8002
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    vci: VCIConfig = field(default_factory=VCIConfig)
    kbs: KBSConfig = field(default_factory=KBSConfig)


def get_config() -> AppConfig:
    return AppConfig()
