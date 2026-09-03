"""Central configuration for PE_TTM_20Q Fund System."""
from __future__ import annotations

import json
import hashlib
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)

MIN_STRATEGY_HOLDINGS = 15

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
    min_holdings: int = MIN_STRATEGY_HOLDINGS
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
    # Costs — Vietnam stock market realistic model
    # Broker commission: ~0.15% each side (buy + sell)
    broker_fee_bps: float = 15.0
    # Sell tax: 0.1% (Government PIT on selling)
    sell_tax_bps: float = 10.0
    # Total round-trip: buy broker (15) + sell broker (15) + sell tax (10) = 40 bps
    transaction_cost_bps: float = 40.0
    # Benchmark
    benchmark_symbol: str = "VNINDEX"
    # Price scale in DB
    close_scale_vnd: float = 1000.0


@dataclass(frozen=True)
class VCIConfig:
    graphql_url: str = "https://trading.vietcap.com.vn/data-mt/graphql"
    ohlc_url: str = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
    rate_limit_rpm: int = 120
    cache_ttl_financial_days: int = 30
    cache_ttl_price_days: int = 1


@dataclass(frozen=True)
class KBSConfig:
    base_url: str = "https://kbbuddywts.kbsec.com.vn/iis-server/investment"
    price_url: str = "https://kbbuddywts.kbsec.com.vn/sas/kbsv-stock-data-store"
    rate_limit_rpm: int = 60
    cache_ttl_hours: int = 24


@dataclass(frozen=True)
class AppConfig:
    db_path: Path = field(default_factory=lambda: Path(
        os.environ.get("PE5Y_DB_PATH", "vietnam_stocks.db")
    ))
    host: str = "127.0.0.1"
    port: int = 8002
    # Explicit local opt-in. This mode can use the immutable vendor snapshot
    # for research planning, but it never changes investment-readiness flags.
    allow_legacy_research_planner: bool = field(default_factory=lambda: (
        os.environ.get(
            "PE5Y_ALLOW_LEGACY_RESEARCH_PLANNER", "0"
        ).strip().lower()
        in {"1", "true", "yes", "on"}
    ))
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    vci: VCIConfig = field(default_factory=VCIConfig)
    kbs: KBSConfig = field(default_factory=KBSConfig)


# --- Mutable config with JSON override support ---

_config: AppConfig | None = None
_config_lock = threading.Lock()
_OVERRIDES_FILE = "strategy_config.json"
# Fields excluded from config UI (internal constants)
_INTERNAL_FIELDS = {"close_scale_vnd", "transaction_cost_bps"}


def _overrides_path() -> Path:
    """Path to strategy config overrides JSON (next to DB file)."""
    db_path = Path(os.environ.get("PE5Y_DB_PATH", "vietnam_stocks.db"))
    return db_path.parent / _OVERRIDES_FILE


def _load_strategy_overrides() -> dict:
    """Load the active database-backed strategy configuration.

    ``strategy_config.json`` remains a read-only compatibility fallback for a
    database that has not yet run the hardening migration.
    """
    db_path = Path(os.environ.get("PE5Y_DB_PATH", "vietnam_stocks.db"))
    if db_path.exists():
        try:
            with sqlite3.connect(str(db_path)) as conn:
                table = conn.execute(
                    """SELECT 1 FROM sqlite_master
                       WHERE type = 'table'
                         AND name = 'strategy_config_versions'"""
                ).fetchone()
                if table:
                    row = conn.execute(
                        """SELECT config_json
                           FROM strategy_config_versions
                           WHERE status = 'active'
                           ORDER BY id DESC LIMIT 1"""
                    ).fetchone()
                    if row:
                        data = json.loads(row[0])
                        if isinstance(data, dict):
                            return data
        except (OSError, sqlite3.Error, ValueError):
            log.exception("Could not read active strategy configuration")

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

    sc_dict["min_holdings"] = max(
        MIN_STRATEGY_HOLDINGS,
        int(sc_dict["min_holdings"]),
    )
    sc_dict["transaction_cost_bps"] = (
        float(sc_dict["broker_fee_bps"]) * 2
        + float(sc_dict["sell_tax_bps"])
    )

    if applied:
        log.info("Strategy config: %d override(s) applied", applied)
    return AppConfig(
        db_path=base.db_path, host=base.host, port=base.port,
        allow_legacy_research_planner=base.allow_legacy_research_planner,
        strategy=StrategyConfig(**sc_dict), vci=base.vci, kbs=base.kbs,
    )


def build_config_with_overrides(overrides: dict) -> AppConfig:
    """Build an isolated candidate config without changing the active config."""
    base = AppConfig()
    values = {
        item.name: getattr(base.strategy, item.name)
        for item in fields(StrategyConfig)
    }
    for key, value in overrides.items():
        if key not in values or key in _INTERNAL_FIELDS:
            continue
        expected = type(values[key])
        if expected is list:
            values[key] = list(value)
        elif expected is int:
            values[key] = int(value)
        elif expected is float:
            values[key] = float(value)
        else:
            values[key] = value
    values["min_holdings"] = max(
        MIN_STRATEGY_HOLDINGS, int(values["min_holdings"])
    )
    values["transaction_cost_bps"] = (
        float(values["broker_fee_bps"]) * 2
        + float(values["sell_tax_bps"])
    )
    return AppConfig(
        db_path=base.db_path,
        host=base.host,
        port=base.port,
        allow_legacy_research_planner=base.allow_legacy_research_planner,
        strategy=StrategyConfig(**values),
        vci=base.vci,
        kbs=base.kbs,
    )


def get_config() -> AppConfig:
    """Get current config (cached, with JSON overrides merged). Thread-safe."""
    global _config
    if _config is not None:
        return _config
    with _config_lock:
        if _config is None:
            _config = _build_config()
        return _config


def reload_config() -> AppConfig:
    """Invalidate cache and reload config from defaults + JSON. Thread-safe."""
    global _config
    with _config_lock:
        _config = _build_config()
        return _config


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
    if "min_holdings" in clean:
        clean["min_holdings"] = max(
            MIN_STRATEGY_HOLDINGS,
            int(clean["min_holdings"]),
        )
    path = _overrides_path()
    path.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    reload_config()
    return get_strategy_dict()


def stage_strategy_config(data: dict) -> dict:
    """Persist a validated candidate config without activating it."""
    active = _load_strategy_overrides()
    known = {item.name for item in fields(StrategyConfig)} - _INTERNAL_FIELDS
    merged = {**active, **{key: value for key, value in data.items() if key in known}}
    candidate = build_config_with_overrides(merged)
    normalized = {
        item.name: getattr(candidate.strategy, item.name)
        for item in fields(StrategyConfig)
        if item.name not in _INTERNAL_FIELDS
    }
    payload = json.dumps(
        normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    db_path = candidate.db_path
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """UPDATE strategy_config_versions
               SET status = 'failed',
                   error = 'Superseded by a newer pending configuration'
               WHERE status = 'pending'"""
        )
        cur = conn.execute(
            """INSERT INTO strategy_config_versions
               (config_json, config_hash, status)
               VALUES (?, ?, 'pending')""",
            (payload, digest),
        )
        pending_id = int(cur.lastrowid)
    return {
        "id": pending_id,
        "status": "pending",
        "config": normalized,
        "config_hash": digest,
    }


def get_strategy_config_state() -> dict:
    """Return active and pending configuration metadata."""
    db_path = Path(os.environ.get("PE5Y_DB_PATH", "vietnam_stocks.db"))
    active = get_strategy_dict()
    pending = None
    if db_path.exists():
        try:
            with sqlite3.connect(str(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """SELECT id, config_json, config_hash, created_at
                       FROM strategy_config_versions
                       WHERE status = 'pending'
                       ORDER BY id DESC LIMIT 1"""
                ).fetchone()
            if row:
                pending = {
                    "id": int(row["id"]),
                    "config": json.loads(row["config_json"]),
                    "config_hash": row["config_hash"],
                    "created_at": row["created_at"],
                }
        except (sqlite3.Error, ValueError):
            log.exception("Could not read pending strategy configuration")
    return {
        "active": active,
        "pending": pending,
        "status": "pending" if pending else "active",
    }


def get_pending_strategy_config() -> tuple[int, AppConfig] | None:
    """Load the newest pending candidate for the snapshot builder."""
    db_path = Path(os.environ.get("PE5Y_DB_PATH", "vietnam_stocks.db"))
    if not db_path.exists():
        return None
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            """SELECT id, config_json
               FROM strategy_config_versions
               WHERE status = 'pending'
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    if not row:
        return None
    return int(row[0]), build_config_with_overrides(json.loads(row[1]))


def activate_strategy_config(config_version_id: int) -> AppConfig:
    """Activate a candidate only after its snapshot was built successfully."""
    db_path = Path(os.environ.get("PE5Y_DB_PATH", "vietnam_stocks.db"))
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            """SELECT id FROM strategy_config_versions
               WHERE id = ? AND status = 'pending'""",
            (config_version_id,),
        ).fetchone()
        if not row:
            raise RuntimeError("Pending strategy configuration no longer exists")
        conn.execute(
            """UPDATE strategy_config_versions
               SET status = 'failed',
                   error = 'Replaced by a newly activated configuration'
               WHERE status = 'active'"""
        )
        conn.execute(
            """UPDATE strategy_config_versions
               SET status = 'active', activated_at = CURRENT_TIMESTAMP,
                   error = NULL
               WHERE id = ?""",
            (config_version_id,),
        )
    return reload_config()


def fail_strategy_config(config_version_id: int, error: str) -> None:
    """Mark a pending candidate failed while retaining the active version."""
    db_path = Path(os.environ.get("PE5Y_DB_PATH", "vietnam_stocks.db"))
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """UPDATE strategy_config_versions
               SET status = 'failed', error = ?
               WHERE id = ? AND status = 'pending'""",
            (error[:500], config_version_id),
        )


def reset_strategy_config() -> dict:
    """Delete overrides file and return defaults."""
    path = _overrides_path()
    if path.exists():
        path.unlink()
    reload_config()
    return get_strategy_dict()


# --- Saved portfolio persistence ---

_PORTFOLIO_FILE = "saved_portfolio.json"


def _portfolio_path() -> Path:
    """Path to saved portfolio JSON (next to DB file)."""
    db_path = Path(os.environ.get("PE5Y_DB_PATH", "vietnam_stocks.db"))
    return db_path.parent / _PORTFOLIO_FILE


def load_saved_portfolio() -> dict | None:
    """Load saved portfolio params. Returns None if not saved."""
    path = _portfolio_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_portfolio(data: dict) -> dict:
    """Save portfolio params to JSON."""
    allowed = {"capital_b", "select_pct", "strategy", "expand_mode", "month"}
    clean = {k: v for k, v in data.items() if k in allowed}
    path = _portfolio_path()
    path.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Saved portfolio: %s", clean)
    return clean


def delete_saved_portfolio() -> bool:
    """Delete saved portfolio file."""
    path = _portfolio_path()
    if path.exists():
        path.unlink()
        return True
    return False
