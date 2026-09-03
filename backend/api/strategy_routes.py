"""Tombstones for strategy endpoints retired from the investment workflow.

The authoritative planner lives under ``/api/fund`` and reads an immutable
cycle snapshot.  Keeping explicit HTTP 410 responses here prevents old
bookmarks or clients from silently recalculating a portfolio from mutable
database tables.
"""
from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/strategy", tags=["retired-strategy-api"])

_RETIRED_DETAIL = (
    "API chiến lược động đã ngừng hoạt động. "
    "Dùng POST /api/fund/portfolio-plan để lập danh mục từ snapshot bất biến."
)


def _gone() -> NoReturn:
    raise HTTPException(status_code=410, detail=_RETIRED_DETAIL)


@router.get("/optimize")
def retired_optimize() -> None:
    _gone()


@router.get("/portfolio")
def retired_portfolio() -> None:
    _gone()


@router.get("/portfolio/saved")
def retired_saved_portfolio_get() -> None:
    _gone()


@router.put("/portfolio/saved")
def retired_saved_portfolio_put() -> None:
    _gone()


@router.delete("/portfolio/saved")
def retired_saved_portfolio_delete() -> None:
    _gone()


@router.get("/history/sensitivity")
def retired_sensitivity() -> None:
    _gone()


@router.get("/history/yearly")
def retired_yearly_performance() -> None:
    _gone()
