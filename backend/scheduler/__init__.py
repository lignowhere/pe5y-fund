"""Background scheduler — auto-updates missing price data on startup + every 6h."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..data.updater import detect_missing_prices, update_prices
from ..data.vci_client import VCIClient

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_price_update(db_path: Path, rate_limit_rpm: int = 30) -> None:
    """Detect missing prices and fetch them. Runs in background thread."""
    try:
        missing = detect_missing_prices(db_path)
        if not missing:
            log.info("Scheduler: no missing prices detected")
            return
        log.info("Scheduler: updating %d symbols with missing prices", len(missing))
        with VCIClient(rate_limit_rpm=rate_limit_rpm) as vci:
            result = update_prices(db_path, missing, vci, count_back=30)
        log.info(
            "Scheduler: updated=%d failed=%d inserted=%d",
            result.symbols_updated,
            result.symbols_failed,
            result.prices_inserted,
        )
        if result.errors:
            for err in result.errors[:5]:
                log.warning("  %s", err)
    except Exception:
        log.exception("Scheduler: price update failed")


def start_scheduler(db_path: Path, rate_limit_rpm: int = 30) -> None:
    """Start APScheduler with an interval job + immediate first run."""
    global _scheduler
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        _run_price_update,
        trigger=IntervalTrigger(hours=6),
        args=[db_path, rate_limit_rpm],
        id="price_update",
        name="Auto-update missing prices",
        replace_existing=True,
        next_run_time=None,  # don't run immediately via scheduler
    )
    _scheduler.start()
    log.info("Scheduler started (6h interval)")

    # Fire first run immediately in a thread (non-blocking)
    import threading
    threading.Thread(
        target=_run_price_update,
        args=(db_path, rate_limit_rpm),
        daemon=True,
        name="initial-price-update",
    ).start()


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Scheduler stopped")
