"""Dynamic market-cap filter: base 200B VND, +10% every 2 years from 2015."""
from __future__ import annotations

import math


def get_min_market_cap(year: int, *, base_vnd: float = 200_000_000_000.0,
                       growth_rate: float = 0.10, growth_period: int = 2,
                       base_year: int = 2015) -> float:
    """Return minimum market cap (VND) for a given year.

    Formula: base_vnd × (1 + growth_rate)^(floor((year - base_year) / growth_period))
    Examples: 2015→200B, 2017→220B, 2019→242B, 2025→322B
    """
    if year < base_year:
        return base_vnd
    periods = math.floor((year - base_year) / growth_period)
    return base_vnd * (1 + growth_rate) ** periods
