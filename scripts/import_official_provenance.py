"""Import a reviewed official-provenance JSON manifest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_config
from backend.data.db_migration import run_migrations
from backend.data.provenance import import_manifest
from backend.data.provenance import activate_official_financial_version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument(
        "--activate-financial-version",
        action="store_true",
        help=(
            "Activate the imported official batch only if every required "
            "symbol is safely classified"
        ),
    )
    args = parser.parse_args()
    db_path = args.db or get_config().db_path
    run_migrations(db_path)
    result = import_manifest(db_path, args.manifest)
    if args.activate_financial_version:
        batch_id = int(result.get("official_batch_id") or 0)
        if not batch_id:
            raise SystemExit(
                "Manifest has no batch/symbol_classifications section"
            )
        result["activation"] = activate_official_financial_version(
            db_path, batch_id
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
