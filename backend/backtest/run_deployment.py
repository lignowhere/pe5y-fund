"""Capital deployment strategy comparison — CLI entry point.

Usage:
    python -m backend.backtest.run_deployment [--output ./output]

Tests: when new capital arrives mid-year, should you
  (A) buy more of the same September stocks, or
  (B) re-run the PE signal and buy fresh top picks?
"""
from __future__ import annotations

import argparse
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
    parser = argparse.ArgumentParser(description="Capital deployment strategy comparison")
    parser.add_argument("--output", default="./output", help="Output directory")
    args = parser.parse_args()

    from backend.config import get_config
    from backend.backtest.capital_deployment_sim import (
        run_deployment_simulation, CashFlowEvent, DeploymentResult,
    )

    cfg = get_config()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    log.info("DB: %s", cfg.db_path)

    # Test config: September rebalance, 10% select (best from previous analysis)
    # Cash flows: +20% at Dec, Mar, Jun (realistic DCA pattern)
    cash_flows = [
        CashFlowEvent(month=12, amount_pct=20.0),
        CashFlowEvent(month=3, amount_pct=20.0),
        CashFlowEvent(month=6, amount_pct=20.0),
    ]

    log.info("=" * 65)
    log.info("Capital Deployment Simulation")
    log.info("Rebalance: September | Select: 10%%")
    log.info("Injections: +20%% at Dec, +20%% at Mar, +20%% at Jun")
    log.info("Strategies: PE5Y, PE_TTM_20Q_RELAXED")
    log.info("Deployment: add_existing vs fresh_signal")
    log.info("Formation years: 2017-2023 (hold 2018-2024)")
    log.info("=" * 65)

    results = run_deployment_simulation(
        cfg.db_path, cfg,
        signal_strategies=["PE5Y", "PE_TTM_20Q_RELAXED"],
        deployment_strategies=["add_existing", "fresh_signal"],
        formation_years=list(range(2017, 2024)),
        rebalance_month=9,
        select_pct=10.0,
        cash_flows=cash_flows,
    )

    # Also test with select_pct=12% for PE_TTM_20Q_RELAXED
    results_12 = run_deployment_simulation(
        cfg.db_path, cfg,
        signal_strategies=["PE_TTM_20Q_RELAXED"],
        deployment_strategies=["add_existing", "fresh_signal"],
        formation_years=list(range(2017, 2024)),
        rebalance_month=9,
        select_pct=12.0,
        cash_flows=cash_flows,
    )

    _print_results(results, results_12)

    # Save JSON
    import json
    from dataclasses import asdict
    data = {
        "pct10": [asdict(r) for r in results],
        "pct12_relaxed": [asdict(r) for r in results_12],
    }
    json_path = out / "deployment-simulation-results.json"
    json_path.write_text(json.dumps(data, indent=2, default=str))
    log.info("Results saved to %s", json_path)


def _print_results(results: list, results_12: list):
    print(f"\n{'='*80}")
    print("  CAPITAL DEPLOYMENT SIMULATION RESULTS")
    print(f"  Rebalance: September | Injections: +20% at Dec, Mar, Jun")
    print(f"{'='*80}")

    print(f"\n  {'Strategy':<22} {'Deploy':<15} {'Pct':>4} "
          f"{'Base CAGR':>10} {'Blend CAGR':>11} {'Delta':>7} "
          f"{'Win%':>5} {'Worst':>7} {'Best':>7}")
    print(f"  {'-'*74}")

    for r in results + results_12:
        delta = (r.cagr_blended - r.cagr_base_only) * 100
        sign = "+" if delta >= 0 else ""
        print(f"  {r.signal_strategy:<22} {r.deployment_strategy:<15} "
              f"{r.select_pct:>3.0f}% "
              f"{r.cagr_base_only*100:>9.2f}% {r.cagr_blended*100:>10.2f}% "
              f"{sign}{delta:>5.2f}pp "
              f"{r.win_rate*100:>4.0f}% "
              f"{r.worst_year*100:>6.1f}% {r.best_year*100:>6.1f}%")

    # Year-by-year detail for best combo
    all_results = results + results_12
    best = max(all_results, key=lambda r: r.cagr_blended)
    print(f"\n{'='*80}")
    print(f"  YEAR-BY-YEAR DETAIL: {best.signal_strategy} + {best.deployment_strategy} "
          f"(pct={best.select_pct:.0f}%)")
    print(f"{'='*80}")
    print(f"  {'Hold':>5} {'Base':>8} {'Blended':>9} {'Delta':>7} {'#Base':>6} {'#Tranch':>8}")
    print(f"  {'-'*50}")
    for yr in best.yearly:
        delta = (yr.blended_return - yr.base_return) * 100
        sign = "+" if delta >= 0 else ""
        print(f"  {yr.hold_year:>5} {yr.base_return*100:>7.1f}% "
              f"{yr.blended_return*100:>8.1f}% {sign}{delta:>5.1f}pp "
              f"{yr.n_stocks_base:>5} {yr.n_tranches:>7}")

    # Comparison: add_existing vs fresh_signal
    print(f"\n{'='*80}")
    print(f"  VERDICT: add_existing vs fresh_signal")
    print(f"{'='*80}")

    for sig in ["PE5Y", "PE_TTM_20Q_RELAXED"]:
        ae = [r for r in all_results if r.signal_strategy == sig and r.deployment_strategy == "add_existing"]
        fs = [r for r in all_results if r.signal_strategy == sig and r.deployment_strategy == "fresh_signal"]
        for a, f in zip(ae, fs):
            diff = (f.cagr_blended - a.cagr_blended) * 100
            winner = "fresh_signal" if diff > 0 else "add_existing"
            print(f"  {sig} (pct={a.select_pct:.0f}%): "
                  f"fresh_signal {'>' if diff > 0 else '<'} add_existing by {abs(diff):.2f}pp "
                  f"→ {winner}")


if __name__ == "__main__":
    main()
