"""Command-line entry point for the hidden Windows data-sync task."""
from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import get_config
from .db_migration import run_migrations
from .sync_service import run_full_sync
from ..utils.backup import ensure_daily_backup


def _exit_code_for_result(result: dict[str, object]) -> int:
    status = result.get("status")
    if status == "already_running":
        return 0
    if status != "completed":
        return 1
    if int(result.get("prices_failed") or 0) > 0:
        return 2
    if int(result.get("financials_failed") or 0) > 0:
        return 2
    return 0


def main() -> int:
    config = get_config()
    log_path = config.db_path.parent / "logs" / "data-sync.log"
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        "%(asctime)sZ %(name)s %(levelname)s %(message)s"
    )
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    ensure_daily_backup(config.db_path, max_backups=5)
    run_migrations(config.db_path)
    result = run_full_sync(config)
    exit_code = _exit_code_for_result(result)
    if exit_code == 2:
        logging.getLogger(__name__).error(
            "Sync completed with partial failures "
            "(prices_failed=%s, financials_failed=%s); "
            "requesting a Scheduled Task retry",
            result.get("prices_failed", 0),
            result.get("financials_failed", 0),
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
