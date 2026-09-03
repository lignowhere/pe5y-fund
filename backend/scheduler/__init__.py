"""In-process fallback scheduler for the shared full-data sync."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import AppConfig, get_config
from ..data.sync_service import run_full_sync

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def _run_data_update(config: AppConfig) -> None:
    try:
        result = run_full_sync(get_config())
        log.info("Scheduler sync result: %s", result)
    except Exception:
        log.exception("Scheduler: full data update failed")


def start_scheduler(config: AppConfig) -> None:
    """Run at 18:30 on weekdays as a fallback to the Windows task."""
    global _scheduler
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        _run_data_update,
        trigger=CronTrigger(day_of_week="mon-fri", hour=18, minute=30),
        args=[config],
        id="full_data_sync",
        name="PE5Y full data sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    log.info("Scheduler started (weekdays at 18:30)")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Scheduler stopped")
