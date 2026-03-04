"""PE5Y vs PE_TTM_20Q Strategy Comparison — CLI entry point.

Usage:
    python -m backend.backtest.run_comparison [--output ./output]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure imports work when run as script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
os.environ.setdefault("PE5Y_DB_PATH", "./vietnam_stocks.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="PE5Y vs PE_TTM_20Q comparison")
    parser.add_argument("--output", default="./output", help="Output directory")
    args = parser.parse_args()

    from backend.config import get_config
    from backend.backtest.sensitivity_runner import run_sensitivity, save_results, save_for_optimizer
    from backend.report.pdf_report import generate_report

    cfg = get_config()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    log.info("DB: %s", cfg.db_path)
    log.info("Output: %s", out.resolve())

    # Pre-flight: check quarterly data coverage
    _preflight(cfg.db_path)

    # Formation years: 2017-2024 for fair comparison (20Q needs data from 2013+)
    fy_common = list(range(2017, 2025))  # 7 years of data

    log.info("=" * 60)
    log.info("Running sensitivity: 12 months x 4 pcts x 3 strategies = 144 combos")
    log.info("Formation years: %s (hold %d-%d)", fy_common, fy_common[0]+1, fy_common[-1]+1)
    log.info("=" * 60)

    runs, vnindex = run_sensitivity(
        cfg.db_path, cfg,
        formation_years_pe5y=fy_common,
        formation_years_20q=fy_common,
    )

    # Save JSON
    json_path = out / "strategy-comparison-results.json"
    save_results(runs, vnindex, json_path)

    # Save optimizer-compatible results next to DB
    save_for_optimizer(runs, vnindex, cfg.db_path.parent)

    # Print summary
    _print_summary(runs, vnindex)

    # Generate PDF
    pdf_path = out / "strategy-comparison-report.pdf"
    generate_report(runs, vnindex, pdf_path, formation_years=fy_common)
    log.info("PDF report: %s", pdf_path.resolve())

    print(f"\nDone! Report: {pdf_path.resolve()}")


def _preflight(db_path: Path):
    """Check quarterly data coverage before running."""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    r = conn.execute(
        "SELECT COUNT(*) FROM financial_ratios WHERE quarter IS NOT NULL AND quarter <= 4"
    ).fetchone()
    q_count = r[0]

    r2 = conn.execute("""
        SELECT COUNT(DISTINCT symbol) FROM (
            SELECT symbol, COUNT(*) as cnt
            FROM financial_ratios
            WHERE quarter IS NOT NULL AND quarter <= 4 AND eps_vnd > 0
            GROUP BY symbol HAVING cnt >= 20
        )
    """).fetchone()
    q_syms = r2[0]
    conn.close()

    log.info("Quarterly data: %d rows, %d symbols with >=20 positive quarters", q_count, q_syms)
    if q_count == 0:
        log.error("No quarterly data in DB! Run quarterly data fetch first.")
        sys.exit(1)
    if q_syms < 50:
        log.warning("Only %d symbols with 20+ quarters — results may be sparse", q_syms)


def _print_summary(runs, vnindex):
    pe5y = sorted([r for r in runs if r.strategy == "PE5Y"],
                  key=lambda r: r.net_cagr, reverse=True)
    pe20q = sorted([r for r in runs if r.strategy == "PE_TTM_20Q"],
                   key=lambda r: r.net_cagr, reverse=True)
    pe20q_r = sorted([r for r in runs if r.strategy == "PE_TTM_20Q_RELAXED"],
                     key=lambda r: r.net_cagr, reverse=True)

    for label, top_runs in [("PE5Y", pe5y), ("PE_TTM_20Q", pe20q),
                            ("PE_TTM_20Q_RELAXED", pe20q_r)]:
        print(f"\n{'='*70}")
        print(f"  {label} TOP 5 CONFIGURATIONS")
        print(f"{'='*70}")
        print(f"  {'Month':>5} {'Pct':>5} {'CAGR':>8} {'Win%':>6} {'AvgN':>5} {'Worst':>8} {'Best':>8}")
        for r in top_runs[:5]:
            print(f"  {r.rebalance_month:>5} {r.select_pct:>5.0f} {r.net_cagr*100:>7.2f}% "
                  f"{r.win_rate*100:>5.0f}% {r.avg_stocks:>5.0f} "
                  f"{r.worst_year*100:>7.1f}% {r.best_year*100:>7.1f}%")

    print(f"\n{'='*70}")
    print(f"  VNINDEX BENCHMARK (buy-and-hold)")
    print(f"{'='*70}")
    for m in sorted(vnindex.keys()):
        print(f"  Month {m:>2}: CAGR {vnindex[m]*100:>7.2f}%")


if __name__ == "__main__":
    main()
