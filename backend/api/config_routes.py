"""Strategy configuration API routes."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import (
    get_strategy_defaults,
    get_strategy_dict,
    get_strategy_config_state,
    stage_strategy_config,
)

router = APIRouter(prefix="/api/strategy", tags=["config"])


class StrategyConfigBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rebalance_month: int | None = Field(default=None, ge=1, le=12)
    select_pcts: list[float] | None = Field(
        default=None, min_length=1, max_length=4
    )
    min_holdings: int | None = Field(default=None, ge=15)
    participation_rate: float | None = Field(default=None, gt=0, le=1)
    accum_days: int | None = Field(default=None, ge=1, le=100)
    lot_size: int | None = Field(default=None, ge=1, le=1_000)
    mcap_base_vnd: float | None = Field(default=None, ge=0)
    mcap_growth_rate: float | None = Field(default=None, ge=0, le=1)
    mcap_growth_period_years: int | None = Field(
        default=None, ge=1, le=20
    )
    mcap_base_year: int | None = Field(default=None, ge=2000, le=2100)
    min_trading_days: int | None = Field(default=None, ge=1, le=366)
    min_avg_dollar_volume_vnd: float | None = Field(default=None, ge=0)
    max_zero_volume_frac: float | None = Field(default=None, ge=0, le=1)
    max_stale_close_frac: float | None = Field(default=None, ge=0, le=1)
    max_rebalance_gap_days: int | None = Field(default=None, ge=1, le=31)
    broker_fee_bps: float | None = Field(default=None, ge=0, le=100)
    sell_tax_bps: float | None = Field(default=None, ge=0, le=100)
    benchmark_symbol: str | None = Field(
        default=None, min_length=2, max_length=12, pattern=r"^[A-Z0-9]+$"
    )

    @field_validator("select_pcts")
    @classmethod
    def validate_select_pcts(
        cls, value: list[float] | None
    ) -> list[float] | None:
        if value is None:
            return None
        normalized = sorted({float(item) for item in value})
        allowed = {10.0, 12.0, 14.0, 16.0}
        if set(normalized) != allowed:
            raise ValueError(
                "select_pcts must contain exactly 10, 12, 14 and 16"
            )
        return normalized


@router.get("/config")
def get_config_endpoint():
    """Get current strategy configuration."""
    state = get_strategy_config_state()
    return {
        **state["active"],
        "_config_status": state["status"],
        "_pending_config": state["pending"],
    }


@router.get("/config/defaults")
def get_defaults_endpoint():
    """Get default strategy configuration (no overrides)."""
    return get_strategy_defaults()


@router.put("/config")
def update_config_endpoint(body: StrategyConfigBody):
    """Stage configuration; it becomes active after snapshot validation."""
    data = body.model_dump(exclude_none=True)
    if not data:
        return get_config_endpoint()
    pending = stage_strategy_config(data)
    return {
        **get_strategy_dict(),
        "_config_status": "pending",
        "_pending_config": pending,
    }


@router.post("/config/reset")
def reset_config_endpoint():
    """Stage defaults; do not invalidate the active snapshot immediately."""
    pending = stage_strategy_config(get_strategy_defaults())
    return {
        **get_strategy_dict(),
        "_config_status": "pending",
        "_pending_config": pending,
    }
