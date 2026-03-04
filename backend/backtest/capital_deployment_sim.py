"""Capital deployment simulation — add_existing vs fresh_signal.

When capital arrives mid-year (outside the main September rebalance),
should you buy more of the same stocks or re-run the signal?

Each "tranche" of capital (initial + injections) is tracked separately:
- Tranche buys at equal weight on its injection date
- All tranches sell at the next rebalance date
- Blended return = weighted average across tranches
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ..config import AppConfig
from ..strategy.signal import generate_signal, select_top_n
from ..strategy.signal_pe_ttm_20q import generate_signal_20q, select_top_n_20q

log = logging.getLogger(__name__)
CLOSE_SCALE_VND = 1000.0
COST_BPS = 10.0  # per side


@dataclass
class CashFlowEvent:
    """Capital injection relative to initial capital."""
    month: int         # calendar month 1-12
    amount_pct: float  # % of initial capital (positive=deposit)


@dataclass
class DeployYearDetail:
    hold_year: int
    base_return: float     # return on September capital only
    blended_return: float  # return on all capital (initial + injections)
    n_stocks_base: int
    n_tranches: int


@dataclass
class DeploymentResult:
    signal_strategy: str     # "PE5Y" / "PE_TTM_20Q_RELAXED"
    deployment_strategy: str  # "add_existing" / "fresh_signal"
    rebalance_month: int
    select_pct: float
    cagr_blended: float = 0.0
    cagr_base_only: float = 0.0
    win_rate: float = 0.0
    worst_year: float = 0.0
    best_year: float = 0.0
    yearly: list[DeployYearDetail] = field(default_factory=list)


def run_deployment_simulation(
    db_path: Path,
    config: AppConfig,
    signal_strategies: list[str] | None = None,
    deployment_strategies: list[str] | None = None,
    formation_years: list[int] | None = None,
    rebalance_month: int = 9,
    select_pct: float = 10.0,
    cash_flows: list[CashFlowEvent] | None = None,
) -> list[DeploymentResult]:
    """Run capital deployment simulation for all strategy combinations."""
    if signal_strategies is None:
        signal_strategies = ["PE5Y", "PE_TTM_20Q_RELAXED"]
    if deployment_strategies is None:
        deployment_strategies = ["add_existing", "fresh_signal"]
    if formation_years is None:
        formation_years = list(range(2017, 2024))
    if cash_flows is None:
        # Default: 3 deposits per year (+20% each = +60% total by year end)
        cash_flows = [
            CashFlowEvent(month=12, amount_pct=20.0),  # Dec: +20%
            CashFlowEvent(month=3, amount_pct=20.0),   # Mar: +20%
            CashFlowEvent(month=6, amount_pct=20.0),   # Jun: +20%
        ]

    results = []
    total = len(signal_strategies) * len(deployment_strategies)
    done = 0

    for sig in signal_strategies:
        for dep in deployment_strategies:
            done += 1
            log.info("[%d/%d] %s + %s ...", done, total, sig, dep)
            result = _simulate_all_years(
                db_path, config, sig, dep,
                formation_years, rebalance_month, select_pct, cash_flows,
            )
            results.append(result)
            log.info("  Blended CAGR %.2f%% | Base CAGR %.2f%%",
                     result.cagr_blended * 100, result.cagr_base_only * 100)

    return results


def _simulate_all_years(
    db_path, config, sig_strategy, dep_strategy,
    formation_years, reb_month, select_pct, cash_flows,
) -> DeploymentResult:
    result = DeploymentResult(
        signal_strategy=sig_strategy,
        deployment_strategy=dep_strategy,
        rebalance_month=reb_month,
        select_pct=select_pct,
    )
    blended_rets = []
    base_rets = []

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        for fy in formation_years:
            hold_yr = fy + 1
            yr = _simulate_year(
                db_path, config, conn,
                sig_strategy, dep_strategy,
                fy, hold_yr, reb_month, select_pct, cash_flows,
            )
            result.yearly.append(yr)
            blended_rets.append(yr.blended_return)
            base_rets.append(yr.base_return)

    if blended_rets:
        n = len(blended_rets)
        cum_b = cum_base = 1.0
        for r in blended_rets:
            cum_b *= (1 + r)
        for r in base_rets:
            cum_base *= (1 + r)
        result.cagr_blended = cum_b ** (1.0 / n) - 1
        result.cagr_base_only = cum_base ** (1.0 / n) - 1
        result.win_rate = sum(1 for r in blended_rets if r > 0) / n
        result.worst_year = min(blended_rets)
        result.best_year = max(blended_rets)

    return result


def _simulate_year(
    db_path, config, conn,
    sig_strategy, dep_strategy,
    formation_year, hold_year, reb_month, select_pct, cash_flows,
) -> DeployYearDetail:
    buy_date = f"{hold_year}-{reb_month:02d}-01"
    sell_date = f"{hold_year + 1}-{reb_month:02d}-01"

    # 1. Base signal (September rebalance)
    base_syms = _get_signal_stocks(
        db_path, config, sig_strategy, formation_year, hold_year,
        buy_date, reb_month, select_pct,
    )
    if not base_syms:
        return DeployYearDetail(hold_year, 0.0, 0.0, 0, 0)

    # 2. Build tranches: (weight, symbols, buy_date_for_tranche)
    tranches: list[tuple[float, list[str], str]] = [(1.0, base_syms, buy_date)]

    for cf in cash_flows:
        if cf.amount_pct == 0:
            continue
        weight = cf.amount_pct / 100.0
        inj_date = _injection_date(hold_year, reb_month, cf.month)

        if dep_strategy == "add_existing":
            tranches.append((weight, base_syms, inj_date))
        else:  # fresh_signal
            inj_fy, inj_hy = _injection_formation_year(hold_year, reb_month, cf.month)
            fresh_syms = _get_signal_stocks(
                db_path, config, sig_strategy, inj_fy, inj_hy,
                inj_date, cf.month, select_pct,
            )
            tranches.append((weight, fresh_syms or base_syms, inj_date))

    # 3. Collect all symbols for sell prices
    all_syms = set()
    for _, syms, _ in tranches:
        all_syms.update(syms)
    sell_prices = _get_prices(conn, list(all_syms), sell_date)

    # 4. Calculate base return (tranche 0 only)
    base_buy = _get_prices(conn, base_syms, buy_date)
    base_return = _equal_weight_return(base_syms, base_buy, sell_prices)

    # 5. Calculate blended return across all tranches
    total_invested = 0.0
    total_final = 0.0
    for weight, syms, t_date in tranches:
        t_buy = _get_prices(conn, syms, t_date)
        t_ret = _equal_weight_return(syms, t_buy, sell_prices)
        total_invested += weight
        total_final += weight * (1 + t_ret)

    blended_return = total_final / total_invested - 1.0 if total_invested > 0 else 0.0

    return DeployYearDetail(
        hold_year=hold_year,
        base_return=base_return,
        blended_return=blended_return,
        n_stocks_base=len(base_syms),
        n_tranches=len(tranches),
    )


# --- Helpers ---

def _injection_date(hold_year: int, reb_month: int, inj_month: int) -> str:
    """Calendar date for an injection during the hold period."""
    if inj_month >= reb_month:
        return f"{hold_year}-{inj_month:02d}-01"
    else:
        return f"{hold_year + 1}-{inj_month:02d}-01"


def _injection_formation_year(
    hold_year: int, reb_month: int, inj_month: int,
) -> tuple[int, int]:
    """Return (formation_year, hold_year) for a fresh signal at injection time.

    - If injection is Oct-Dec of hold_year: same formation_year as base
    - If injection is Jan-Aug of next year: formation_year advances by 1
      (because annual data for hold_year is available by then)
    """
    if inj_month >= reb_month:
        return hold_year - 1, hold_year
    else:
        return hold_year, hold_year + 1


def _get_signal_stocks(
    db_path, config, strategy, formation_year, hold_year,
    rebalance_date, rebalance_month, select_pct,
) -> list[str]:
    """Generate signal and return top N% stock symbols."""
    if strategy == "PE5Y":
        cands = generate_signal(
            db_path, formation_year, config,
            hold_year=hold_year, rebalance_date=rebalance_date,
        )
        selected = select_top_n(cands, select_pct)
    elif strategy in ("PE_TTM_20Q", "PE_TTM_20Q_RELAXED"):
        relaxed = strategy == "PE_TTM_20Q_RELAXED"
        cands = generate_signal_20q(
            db_path, formation_year, config,
            hold_year=hold_year, rebalance_date=rebalance_date,
            rebalance_month=rebalance_month,
            require_all_positive=not relaxed,
        )
        selected = select_top_n_20q(cands, select_pct)
    else:
        return []
    return [c.symbol for c in selected]


def _get_prices(
    conn: sqlite3.Connection, symbols: list[str], target_date: str,
    max_gap: int = 10,
) -> dict[str, float]:
    """Get first available close price on/after target_date (in VND)."""
    if not symbols:
        return {}
    from datetime import datetime, timedelta
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    end = (dt + timedelta(days=max_gap)).strftime("%Y-%m-%d")
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(f"""
        SELECT h.symbol, h.close FROM stock_price_history h
        INNER JOIN (
            SELECT symbol, MIN(time) as ft FROM stock_price_history
            WHERE symbol IN ({placeholders}) AND time >= ? AND time <= ?
            AND close IS NOT NULL GROUP BY symbol
        ) sub ON h.symbol = sub.symbol AND h.time = sub.ft
    """, symbols + [target_date, end]).fetchall()
    return {r["symbol"]: float(r["close"]) * CLOSE_SCALE_VND for r in rows}


def _equal_weight_return(
    symbols: list[str], buy_prices: dict, sell_prices: dict,
) -> float:
    """Equal-weight portfolio return, net of round-trip costs."""
    rets = []
    for sym in symbols:
        bp = buy_prices.get(sym, 0)
        sp = sell_prices.get(sym, 0)
        if bp > 0 and sp > 0:
            rets.append(sp / bp - 1.0 - COST_BPS * 2 / 10_000)
    return sum(rets) / len(rets) if rets else 0.0
