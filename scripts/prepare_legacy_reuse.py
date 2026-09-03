"""Reconcile stored research rows and build the official-evidence work queue."""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_config
from backend.data.db_migration import run_migrations
from backend.data.legacy_reuse import (
    build_legacy_reuse_inventory,
    reconcile_vendor_research_versions,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path)
    parser.add_argument("--start-year", type=int, default=2016)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--focus-symbol", action="append", default=[])
    parser.add_argument(
        "--reconcile-stored-research",
        action="store_true",
        help=(
            "Fill missing vendor research keys from stored versions. "
            "This never marks the result official or PIT-ready."
        ),
    )
    args = parser.parse_args()
    db_path = args.db or get_config().db_path
    run_migrations(db_path)
    output: dict[str, object] = {}
    if args.reconcile_stored_research:
        output["research_reconciliation"] = (
            reconcile_vendor_research_versions(db_path)
        )
    result = build_legacy_reuse_inventory(
        db_path,
        start_year=args.start_year,
        end_year=args.end_year,
        focus_symbols=args.focus_symbol,
    )
    output["inventory"] = dataclasses.asdict(result)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
