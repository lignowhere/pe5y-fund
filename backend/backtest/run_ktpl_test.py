"""KTPL-adjusted PE_TTM_20Q backtest — compare adjusted vs unadjusted.

Usage:
    python -m backend.backtest.run_ktpl_test
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
os.environ.setdefault("PE5Y_DB_PATH", "./vietnam_stocks.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main():
    from backend.config import get_config
    from backend.backtest.sensitivity_runner import _backtest_single

    cfg = get_config()
    db_path = cfg.db_path
    formation_years = list(range(2017, 2024))

    # Test focused: September rebalance, select_pct = [10, 12, 14]
    strategies = ["PE_TTM_20Q_RELAXED", "PE_TTM_20Q_KTPL"]
    pcts = [10.0, 12.0, 14.0]
    month = 9

    log.info("=" * 65)
    log.info("KTPL Adjustment Test")
    log.info("Rebalance: September | Formation: 2017-2023")
    log.info("Comparing: PE_TTM_20Q_RELAXED vs PE_TTM_20Q_KTPL")
    log.info("KTPL method: 5yr avg forensic leakage, capped at 50%%")
    log.info("=" * 65)

    results = []
    total = len(strategies) * len(pcts)
    done = 0

    for strat in strategies:
        for pct in pcts:
            done += 1
            log.info("[%d/%d] %s pct=%.0f%% ...", done, total, strat, pct)
            run = _backtest_single(db_path, cfg, strat, month, pct, formation_years)
            results.append(run)
            log.info("  → CAGR %.2f%% | Win %.0f%% | AvgN %.0f | Worst %.1f%% | Best %.1f%%",
                     run.net_cagr * 100, run.win_rate * 100, run.avg_stocks,
                     run.worst_year * 100, run.best_year * 100)

    # Also test months 8 and 3 for robustness
    extra_months = [8, 3]
    for m in extra_months:
        for strat in strategies:
            done += 1
            log.info("[extra] %s M%02d pct=10%% ...", strat, m)
            run = _backtest_single(db_path, cfg, strat, m, 10.0, formation_years)
            results.append(run)
            log.info("  → CAGR %.2f%%", run.net_cagr * 100)

    _print_results(results)

    # Year-by-year detail
    relaxed_9_10 = [r for r in results if r.strategy == "PE_TTM_20Q_RELAXED"
                    and r.rebalance_month == 9 and r.select_pct == 10.0]
    ktpl_9_10 = [r for r in results if r.strategy == "PE_TTM_20Q_KTPL"
                 and r.rebalance_month == 9 and r.select_pct == 10.0]

    if relaxed_9_10 and ktpl_9_10:
        _print_yearly(relaxed_9_10[0], ktpl_9_10[0])


def _print_results(results):
    print(f"\n{'='*80}")
    print(f"  KTPL ADJUSTMENT TEST RESULTS")
    print(f"{'='*80}")
    print(f"  {'Strategy':<24} {'M':>2} {'Pct':>4} {'CAGR':>8} "
          f"{'Win%':>5} {'AvgN':>5} {'Worst':>7} {'Best':>7}")
    print(f"  {'-'*68}")

    for r in results:
        print(f"  {r.strategy:<24} {r.rebalance_month:>2} {r.select_pct:>3.0f}% "
              f"{r.net_cagr*100:>7.2f}% {r.win_rate*100:>4.0f}% "
              f"{r.avg_stocks:>5.0f} {r.worst_year*100:>6.1f}% {r.best_year*100:>6.1f}%")

    # Summary comparison
    print(f"\n{'='*80}")
    print(f"  DELTA: KTPL - RELAXED (positive = KTPL better)")
    print(f"{'='*80}")

    from collections import defaultdict
    by_key = defaultdict(dict)
    for r in results:
        key = (r.rebalance_month, r.select_pct)
        by_key[key][r.strategy] = r

    for (m, pct), strats in sorted(by_key.items()):
        rlx = strats.get("PE_TTM_20Q_RELAXED")
        ktpl = strats.get("PE_TTM_20Q_KTPL")
        if rlx and ktpl:
            delta = (ktpl.net_cagr - rlx.net_cagr) * 100
            sign = "+" if delta >= 0 else ""
            print(f"  M{m:02d} P{pct:.0f}%: {sign}{delta:.2f}pp "
                  f"(KTPL {ktpl.net_cagr*100:.2f}% vs RLX {rlx.net_cagr*100:.2f}%)")


def _print_yearly(relaxed, ktpl):
    print(f"\n{'='*80}")
    print(f"  YEAR-BY-YEAR: RELAXED vs KTPL (M09, 10%)")
    print(f"{'='*80}")
    print(f"  {'Hold':>5} {'RELAXED':>9} {'#':>3} {'KTPL':>9} {'#':>3} {'Delta':>7}")
    print(f"  {'-'*42}")
    for yr_r, yr_k in zip(relaxed.yearly_returns, ktpl.yearly_returns):
        delta = (yr_k.portfolio_return - yr_r.portfolio_return) * 100
        sign = "+" if delta >= 0 else ""
        print(f"  {yr_r.hold_year:>5} {yr_r.portfolio_return*100:>8.1f}% {yr_r.n_stocks:>3}"
              f" {yr_k.portfolio_return*100:>8.1f}% {yr_k.n_stocks:>3}"
              f" {sign}{delta:>5.1f}pp")


if __name__ == "__main__":
    main()
