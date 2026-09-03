"""Back up, attest and activate the existing local financial database.

Usage:
    python -m backend.backtest.activate_trusted_local
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..config import (
    get_config,
    get_pending_strategy_config,
    reload_config,
)
from ..data.db_migration import run_migrations
from ..data.vci_client import VCIClient
from ..fund.snapshots import (
    build_and_activate_trusted_local_snapshot_set,
)
from ..fund.trusted_local import (
    create_trusted_local_attestation,
    sha256_file,
)
from ..utils.backup import backup_database


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Use the existing vietnam_stocks.db under an explicit "
            "owner-confirmed trust label"
        )
    )
    parser.add_argument(
        "--output",
        default="./output/trusted-local-activation.json",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=5_000_000_000,
    )
    args = parser.parse_args()

    config = get_config()
    source_database_sha256 = sha256_file(config.db_path)
    backup_path = backup_database(
        config.db_path,
        config.db_path.parent / "backups",
        max_backups=10,
    )

    # The backup is intentionally completed before any schema migration.
    run_migrations(config.db_path)
    attestation = create_trusted_local_attestation(
        config.db_path,
        backup_path,
        source_database_sha256=source_database_sha256,
    )

    pending = get_pending_strategy_config()
    pending_config_id = pending[0] if pending else None
    snapshot_config = pending[1] if pending else config
    with VCIClient(snapshot_config.vci.rate_limit_rpm) as vci:
        snapshot = build_and_activate_trusted_local_snapshot_set(
            snapshot_config,
            capital_vnd=args.capital,
            adjusted_price_client=vci,
            require_adjusted_prices=True,
            pending_config_version_id=pending_config_id,
        )
    if pending_config_id is not None:
        reload_config()

    result = {
        "backup": {
            "path": str(backup_path.resolve()),
            "sha256": attestation["source_backup_sha256"],
        },
        "attestation": attestation,
        "snapshot": snapshot,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

