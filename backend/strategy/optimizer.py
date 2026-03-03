"""Capital → optimal PE5Y config selector.

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
from .signal import generate_signal, select_top_n


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
) -> list[ConfigResult]:
    """Compare select_pct options and recommend the best one.

    For each select_pct:
      1. Generate signal → rank all candidates
      2. Select top N by pct
      3. Size portfolio with ADV constraints
      4. Score: highest CAGR where fill_rate >= 85%
    """
    import datetime
    if formation_year is None:
        formation_year = datetime.date.today().year - 1
    hold_year = formation_year + 1
    rebal_date = f"{hold_year}-09-01"
    today = datetime.date.today().isoformat()
    rebalance_date = rebal_date if rebal_date <= today else None

    candidates = generate_signal(
        db_path, formation_year, config,
        hold_year=hold_year, rebalance_date=rebalance_date,
    )
    if not candidates:
        return []

    sc = config.strategy
    historical = _load_sensitivity_data(db_path.parent)
    results: list[ConfigResult] = []

    for pct in sc.select_pcts:
        selected = select_top_n(candidates, pct, min_holdings=10)
        symbols = [c.symbol for c in selected]

        positions = size_portfolio(
            db_path, symbols, capital_vnd,
            accum_days=sc.accum_days,
            participation_rate=sc.participation_rate,
            lot_size=sc.lot_size,
        )
        summary = portfolio_summary(positions, capital_vnd)
        cagr = historical.get(f"09-{pct:.0f}")

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
        project_dir / "sensitivity-pe5y-results.json",
        project_dir / "output" / "sensitivity-pe5y-results.json",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            runs = data.get("runs", []) if isinstance(data, dict) else data
            result = {}
            for entry in runs:
                mm_dd = entry.get("rebalance_mm_dd", "")
                pct = entry.get("select_pct")
                metrics = entry.get("metrics", {})
                cagr = metrics.get("net_cagr")
                if mm_dd and pct is not None and cagr is not None:
                    month = mm_dd.split("-")[0]
                    key = f"{int(month):02d}-{float(pct):.0f}"
                    result[key] = round(float(cagr) * 100, 2)
            return result
        except Exception:
            continue
    return {}
