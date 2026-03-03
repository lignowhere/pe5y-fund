"""Central configuration for PE5Y Fund System."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)

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
    # Benchmark
    benchmark_symbol: str = "VNINDEX"
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


# --- Mutable config with JSON override support ---

_config: AppConfig | None = None
_OVERRIDES_FILE = "strategy_config.json"
# Fields excluded from config UI (internal constants)
_INTERNAL_FIELDS = {"close_scale_vnd"}


def _overrides_path() -> Path:
    """Path to strategy config overrides JSON (next to DB file)."""
    db_path = Path(os.environ.get("PE5Y_DB_PATH", "vietnam_stocks.db"))
    return db_path.parent / _OVERRIDES_FILE


def _load_strategy_overrides() -> dict:
    """Load strategy overrides from JSON file."""
    path = _overrides_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _build_config() -> AppConfig:
    """Build AppConfig merging defaults with JSON overrides."""
    base = AppConfig()
    overrides = _load_strategy_overrides()
    if not overrides:
        return base

    sc_dict = {f.name: getattr(base.strategy, f.name) for f in fields(StrategyConfig)}
    applied = 0
    for k, v in overrides.items():
        if k not in sc_dict:
            continue
        expected = type(sc_dict[k])
        try:
            if expected == list:
                sc_dict[k] = v if isinstance(v, list) else [v]
            elif expected == int:
                sc_dict[k] = int(v)
            elif expected == float:
                sc_dict[k] = float(v)
            else:
                sc_dict[k] = v
            applied += 1
        except (TypeError, ValueError):
            pass

    if applied:
        log.info("Strategy config: %d override(s) applied", applied)
    return AppConfig(
        db_path=base.db_path, host=base.host, port=base.port,
        strategy=StrategyConfig(**sc_dict), vci=base.vci, kbs=base.kbs,
    )


def get_config() -> AppConfig:
    """Get current config (cached, with JSON overrides merged)."""
    global _config
    if _config is None:
        _config = _build_config()
    return _config


def reload_config() -> AppConfig:
    """Invalidate cache and reload config from defaults + JSON."""
    global _config
    _config = None
    return get_config()


def get_strategy_dict() -> dict:
    """Get current strategy config as JSON-serializable dict."""
    sc = get_config().strategy
    return {f.name: getattr(sc, f.name) for f in fields(StrategyConfig)
            if f.name not in _INTERNAL_FIELDS}


def get_strategy_defaults() -> dict:
    """Get default strategy config (no overrides)."""
    sc = StrategyConfig()
    return {f.name: getattr(sc, f.name) for f in fields(StrategyConfig)
            if f.name not in _INTERNAL_FIELDS}


def save_strategy_config(data: dict) -> dict:
    """Save strategy config overrides to JSON and reload."""
    known = {f.name for f in fields(StrategyConfig)} - _INTERNAL_FIELDS
    clean = {k: v for k, v in data.items() if k in known}
    path = _overrides_path()
    path.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    reload_config()
    return get_strategy_dict()


def reset_strategy_config() -> dict:
    """Delete overrides file and return defaults."""
    path = _overrides_path()
    if path.exists():
        path.unlink()
    reload_config()
    return get_strategy_dict()
