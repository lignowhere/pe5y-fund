"""Sensitivity backtest — compare PE5Y vs PE_TTM_20Q across months and pcts."""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from ..config import AppConfig
from ..strategy.benchmark import calc_benchmark_cagr
from ..strategy.ktpl_adjustment import build_leakage_lookup
from ..strategy.signal import generate_signal, select_top_n
from ..strategy.signal_pe_ttm_20q import generate_signal_20q, select_top_n_20q

log = logging.getLogger(__name__)
CLOSE_SCALE_VND = 1000.0
COST_BPS = 10.0  # per side → 20 bps round-trip


@dataclass
class YearReturn:
    hold_year: int
    portfolio_return: float  # net of costs
    n_stocks: int


@dataclass
class SensitivityRun:
    strategy: str
    rebalance_month: int
    select_pct: float
    net_cagr: float = 0.0
    yearly_returns: list[YearReturn] = field(default_factory=list)
    avg_stocks: float = 0.0
    win_rate: float = 0.0
    worst_year: float = 0.0
    best_year: float = 0.0


def run_sensitivity(
    db_path: Path,
    config: AppConfig,
    months: Optional[list[int]] = None,
    select_pcts: Optional[list[float]] = None,
    formation_years_pe5y: Optional[list[int]] = None,
    formation_years_20q: Optional[list[int]] = None,
) -> tuple[list[SensitivityRun], dict[int, float]]:
    """Run full sensitivity: PE5Y + PE_TTM_20Q across months x pcts.

    Returns (runs, vnindex_by_month).
    """
    if months is None:
        months = list(range(1, 13))
    if select_pcts is None:
        select_pcts = [10.0, 12.0, 14.0, 16.0]
    if formation_years_pe5y is None:
        formation_years_pe5y = list(range(2017, 2025))
    if formation_years_20q is None:
        formation_years_20q = list(range(2017, 2025))

    runs: list[SensitivityRun] = []
    total = len(months) * len(select_pcts) * 3  # 3 strategies
    done = 0

    for month in months:
        for pct in select_pcts:
            # PE5Y
            run_pe5y = _backtest_single(
                db_path, config, "PE5Y", month, pct, formation_years_pe5y,
            )
            runs.append(run_pe5y)
            done += 1
            log.info("[%d/%d] PE5Y M%02d P%.0f → CAGR %.2f%%",
                     done, total, month, pct, run_pe5y.net_cagr * 100)

            # PE_TTM_20Q (strict: all 20 quarters positive)
            run_20q = _backtest_single(
                db_path, config, "PE_TTM_20Q", month, pct, formation_years_20q,
            )
            runs.append(run_20q)
            done += 1
            log.info("[%d/%d] PE_TTM_20Q M%02d P%.0f → CAGR %.2f%%",
                     done, total, month, pct, run_20q.net_cagr * 100)

            # PE_TTM_20Q_RELAXED (only avg EPS > 0)
            run_20q_r = _backtest_single(
                db_path, config, "PE_TTM_20Q_RELAXED", month, pct, formation_years_20q,
            )
            runs.append(run_20q_r)
            done += 1
            log.info("[%d/%d] PE_TTM_20Q_RELAXED M%02d P%.0f → CAGR %.2f%%",
                     done, total, month, pct, run_20q_r.net_cagr * 100)

    # VNINDEX benchmark per month
    vnindex: dict[int, float] = {}
    start_y = min(formation_years_pe5y) + 1
    end_y = max(formation_years_pe5y) + 1
    for month in months:
        cagr = calc_benchmark_cagr(
            db_path, symbol="VNINDEX",
            rebalance_month=month, start_year=start_y, end_year=end_y,
        )
        vnindex[month] = (cagr / 100.0) if cagr is not None else 0.0

    return runs, vnindex


def _backtest_single(
    db_path: Path, config: AppConfig,
    strategy: str, month: int, pct: float,
    formation_years: list[int],
) -> SensitivityRun:
    """Single backtest: one strategy, one month, one select_pct."""
    run = SensitivityRun(strategy=strategy, rebalance_month=month, select_pct=pct)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        annual_rets: list[float] = []
        stocks_counts: list[int] = []

        for fy in formation_years:
            hold_yr = fy + 1
            buy_date = f"{hold_yr}-{month:02d}-01"
            sell_date = f"{hold_yr + 1}-{month:02d}-01"

            # Generate signal
            if strategy == "PE5Y":
                cands = generate_signal(
                    db_path, fy, config,
                    hold_year=hold_yr, rebalance_date=buy_date,
                )
                selected = select_top_n(cands, pct)
                sel_syms = [c.symbol for c in selected]
            elif strategy == "PE_TTM_20Q":
                cands = generate_signal_20q(
                    db_path, fy, config,
                    hold_year=hold_yr, rebalance_date=buy_date,
                    rebalance_month=month, require_all_positive=True,
                )
                selected = select_top_n_20q(cands, pct)
                sel_syms = [c.symbol for c in selected]
            elif strategy == "PE_TTM_20Q_RELAXED":
                cands = generate_signal_20q(
                    db_path, fy, config,
                    hold_year=hold_yr, rebalance_date=buy_date,
                    rebalance_month=month, require_all_positive=False,
                )
                selected = select_top_n_20q(cands, pct)
                sel_syms = [c.symbol for c in selected]
            elif strategy == "PE_TTM_20Q_KTPL":
                ktpl = build_leakage_lookup(db_path, as_of_year=fy)
                cands = generate_signal_20q(
                    db_path, fy, config,
                    hold_year=hold_yr, rebalance_date=buy_date,
                    rebalance_month=month, require_all_positive=False,
                    ktpl_leakage=ktpl,
                )
                selected = select_top_n_20q(cands, pct)
                sel_syms = [c.symbol for c in selected]
            else:
                sel_syms = []

            if not sel_syms:
                annual_rets.append(0.0)
                stocks_counts.append(0)
                run.yearly_returns.append(YearReturn(hold_yr, 0.0, 0))
                continue

            bp = _get_prices(conn, sel_syms, buy_date)
            sp = _get_prices(conn, sel_syms, sell_date)

            stock_rets = []
            for sym in sel_syms:
                b, s = bp.get(sym), sp.get(sym)
                if b and s and b > 0:
                    net_ret = s / b - 1.0 - COST_BPS * 2 / 10_000
                    stock_rets.append(net_ret)

            port_ret = sum(stock_rets) / len(stock_rets) if stock_rets else 0.0
            annual_rets.append(port_ret)
            stocks_counts.append(len(stock_rets))
            run.yearly_returns.append(YearReturn(hold_yr, port_ret, len(stock_rets)))

    # Aggregate metrics
    if annual_rets:
        cum = 1.0
        for r in annual_rets:
            cum *= (1 + r)
        n = len(annual_rets)
        run.net_cagr = cum ** (1.0 / n) - 1 if n > 0 else 0.0
        run.avg_stocks = sum(stocks_counts) / len(stocks_counts) if stocks_counts else 0
        run.win_rate = sum(1 for r in annual_rets if r > 0) / len(annual_rets)
        run.worst_year = min(annual_rets)
        run.best_year = max(annual_rets)

    return run


def _get_prices(conn: sqlite3.Connection, symbols: list[str],
                target_date: str, max_gap: int = 10) -> dict[str, float]:
    """Get first available close price on or after target_date (VND)."""
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


def save_results(
    runs: list[SensitivityRun],
    vnindex: dict[int, float],
    output_path: Path,
) -> None:
    """Save sensitivity results to JSON."""
    data = {
        "runs": [asdict(r) for r in runs],
        "vnindex_by_month": {str(k): v for k, v in vnindex.items()},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    log.info("Results saved to %s", output_path)


def save_for_optimizer(
    runs: list[SensitivityRun],
    vnindex: dict[int, float],
    db_dir: Path,
) -> None:
    """Save sensitivity-results.json next to DB for optimizer consumption.

    Exports all runs (optimizer filters by strategy internally).
    """
    out_path = db_dir / "sensitivity-results.json"
    data = {
        "runs": [asdict(r) for r in runs],
        "vnindex_by_month": {str(k): v for k, v in vnindex.items()},
    }
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    log.info("Optimizer results saved to %s", out_path)
