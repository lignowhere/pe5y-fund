"""PE5Y Fund System — FastAPI entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.config_routes import router as config_router
from .api.data_routes import router as data_router
from .api.strategy_routes import router as strategy_router
from .api.verify_routes import router as verify_router
from .config import get_config
from .data.db_migration import run_migrations
from .scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

_cfg = get_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One-time DB migrations (idempotent)
    run_migrations(_cfg.db_path)
    # Startup: launch background scheduler for missing data
    start_scheduler(_cfg.db_path, _cfg.vci.rate_limit_rpm)
    yield
    # Shutdown: stop scheduler gracefully
    stop_scheduler()


app = FastAPI(
    title="PE5Y Fund System",
    version="0.1.0",
    description="PE5Y strategy optimizer with data verification",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(config_router)
app.include_router(data_router)
app.include_router(verify_router)
app.include_router(strategy_router)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "db_path": str(_cfg.db_path),
        "db_exists": _cfg.db_path.exists(),
    }
