"""Re-run snapshot-backed strategy tests and activate immutable cycles.

Usage:
    python -m backend.backtest.rebuild_snapshots
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..config import get_config
from ..data.db_migration import run_migrations
from ..fund.snapshots import build_and_activate_snapshot_set
from ..data.vci_client import VCIClient


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backtest and rebuild immutable strategy-cycle snapshots"
    )
    parser.add_argument(
        "--output",
        default="./output/strategy-snapshot-backtest.json",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=5_000_000_000,
    )
    args = parser.parse_args()

    config = get_config()
    run_migrations(config.db_path)
    with VCIClient(config.vci.rate_limit_rpm) as vci:
        result = build_and_activate_snapshot_set(
            config,
            capital_vnd=args.capital,
            adjusted_price_client=vci,
            require_adjusted_prices=True,
        )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
