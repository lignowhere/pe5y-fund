"""PE_TTM_20Q Fund System — FastAPI entry point."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.config_routes import router as config_router
from .api.data_routes import router as data_router
from .api.fund_routes import router as fund_router
from .api.strategy_routes import router as strategy_router
from .api.verify_routes import router as verify_router
from .config import get_config
from .data.db_migration import run_migrations
from .scheduler import start_scheduler, stop_scheduler
from .utils.backup import ensure_daily_backup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

_cfg = get_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _cfg.db_path.exists():
        ensure_daily_backup(_cfg.db_path, max_backups=5)
    run_migrations(_cfg.db_path)
    # Windows Scheduled Task is the single production scheduler.  The
    # in-process scheduler is an explicit development fallback only.
    scheduler_enabled = os.environ.get("PE5Y_ENABLE_INPROCESS_SCHEDULER") == "1"
    if scheduler_enabled:
        start_scheduler(_cfg)
    yield
    if scheduler_enabled:
        stop_scheduler()


app = FastAPI(
    title="PE_TTM_20Q Fund System",
    version="1.0.0",
    description="Snapshot-backed PE5Y fund planner with PIT verification",
    lifespan=lifespan,
)

# CORS: configurable via CORS_ORIGINS env var (comma-separated)
_cors_origins = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins if o.strip()],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)

app.include_router(config_router)
app.include_router(data_router)
app.include_router(verify_router)
app.include_router(strategy_router)
app.include_router(fund_router)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "db_path": str(_cfg.db_path),
        "db_exists": _cfg.db_path.exists(),
    }
