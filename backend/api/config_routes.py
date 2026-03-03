"""Strategy configuration API routes."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import (
    get_strategy_defaults,
    get_strategy_dict,
    reset_strategy_config,
    save_strategy_config,
)

router = APIRouter(prefix="/api/strategy", tags=["config"])


class StrategyConfigBody(BaseModel):
    rebalance_month: int | None = None
    select_pcts: list[float] | None = None
    participation_rate: float | None = None
    accum_days: int | None = None
    lot_size: int | None = None
    mcap_base_vnd: float | None = None
    mcap_growth_rate: float | None = None
    mcap_growth_period_years: int | None = None
    mcap_base_year: int | None = None
    min_trading_days: int | None = None
    min_avg_dollar_volume_vnd: float | None = None
    max_zero_volume_frac: float | None = None
    max_stale_close_frac: float | None = None
    max_rebalance_gap_days: int | None = None
    transaction_cost_bps: float | None = None


@router.get("/config")
def get_config_endpoint():
    """Get current strategy configuration."""
    return get_strategy_dict()


@router.get("/config/defaults")
def get_defaults_endpoint():
    """Get default strategy configuration (no overrides)."""
    return get_strategy_defaults()


@router.put("/config")
def update_config_endpoint(body: StrategyConfigBody):
    """Update strategy configuration (full replacement)."""
    data = body.model_dump(exclude_none=True)
    if not data:
        return get_strategy_dict()
    return save_strategy_config(data)


@router.post("/config/reset")
def reset_config_endpoint():
    """Reset strategy configuration to defaults."""
    return reset_strategy_config()
