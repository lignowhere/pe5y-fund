"""Capital → optimal PE_TTM_20Q config selector.

Compares select_pct options (10/12/14/16%), evaluates ADV capacity,
and recommends the config with highest expected return where fill_rate >= 85%.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import AppConfig
from .position_sizer import portfolio_summary, size_portfolio
from .signal_pe_ttm_20q import generate_signal_20q, select_top_n_20q


@dataclass
class ConfigResult:
    select_pct: float
    stock_count: int
    total_deployed_vnd: float
    cash_drag_pct: float
    avg_fill_rate: float
    max_days_needed: int
    historical_cagr: Optional[float] = None
    recommended: bool = False


def optimize(
    capital_vnd: float,
    db_path: Path,
    config: AppConfig,
    formation_year: Optional[int] = None,
    rebalance_month: Optional[int] = None,
    require_all_positive: bool = False,
    require_last_n_positive: int = 0,
) -> list[ConfigResult]:
    """Compare select_pct options and recommend the best one.

    For each select_pct:
      1. Generate signal → rank all candidates
      2. Select top N by pct
      3. Size portfolio with ADV constraints
      4. Score: highest CAGR where fill_rate >= 85%
    """
    import datetime
    sc = config.strategy
    month = rebalance_month or sc.rebalance_month
    if formation_year is None:
        formation_year = datetime.date.today().year - 1
    hold_year = formation_year + 1
    rebal_date = f"{hold_year}-{month:02d}-01"
    today = datetime.date.today().isoformat()
    rebalance_date = rebal_date if rebal_date <= today else None

    candidates = generate_signal_20q(
        db_path, formation_year, config,
        hold_year=hold_year, rebalance_date=rebalance_date,
        rebalance_month=month, require_all_positive=require_all_positive,
        require_last_n_positive=require_last_n_positive,
    )
    if not candidates:
        return []

    historical = _load_sensitivity_data(db_path.parent)
    results: list[ConfigResult] = []

    for pct in sc.select_pcts:
        selected = select_top_n_20q(candidates, pct, min_holdings=10)
        symbols = [c.symbol for c in selected]

        positions = size_portfolio(
            db_path, symbols, capital_vnd,
            accum_days=sc.accum_days,
            participation_rate=sc.participation_rate,
            lot_size=sc.lot_size,
        )
        summary = portfolio_summary(positions, capital_vnd)
        cagr = historical.get(f"{month:02d}-{pct:.0f}")

        results.append(ConfigResult(
            select_pct=pct,
            stock_count=summary["stock_count"],
            total_deployed_vnd=summary["total_deployed_vnd"],
            cash_drag_pct=summary["cash_drag_pct"],
            avg_fill_rate=summary["avg_fill_rate"],
            max_days_needed=summary["max_days_needed"],
            historical_cagr=cagr,
        ))

    # Recommend: highest CAGR where fill >= 85% and max_days <= accum_days * 3
    max_accum = sc.accum_days * 3  # e.g. 30 days
    eligible = [r for r in results
                if r.avg_fill_rate >= 0.85 and r.max_days_needed <= max_accum]
    if not eligible:
        eligible = [r for r in results if r.avg_fill_rate >= 0.85]
    if not eligible:
        eligible = results
    best = max(eligible, key=lambda r: r.historical_cagr or 0, default=None)
    if best:
        best.recommended = True

    return results


def _load_sensitivity_data(project_dir: Path) -> dict[str, float]:
    """Load historical CAGR from sensitivity results JSON.

    Returns dict with key format "MM-PCT" e.g. "09-14" for September 14%.
    """
    candidates = [
        project_dir / "sensitivity-results.json",
        project_dir / "output" / "sensitivity-results.json",
        project_dir / "output" / "strategy-comparison-results.json",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            runs = data.get("runs", []) if isinstance(data, dict) else data
            result = {}
            for entry in runs:
                strategy = entry.get("strategy", "")
                if strategy != "PE_TTM_20Q_RELAXED":
                    continue
                month = entry.get("rebalance_month")
                pct = entry.get("select_pct")
                cagr = entry.get("net_cagr")
                if month is not None and pct is not None and cagr is not None:
                    key = f"{int(month):02d}-{float(pct):.0f}"
                    result[key] = round(float(cagr) * 100, 2)
            if result:
                return result
        except Exception:
            continue
    return {}
