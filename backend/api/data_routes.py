"""Data management API routes — status, update triggers, search."""
from __future__ import annotations

import json
import logging
import threading
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

log = logging.getLogger(__name__)

from ..config import get_config
from ..data.updater import (
    detect_missing_financials,
    detect_missing_prices,
    get_data_status,
    get_db_health,
)
from ..data.sync_service import (
    get_sync_status,
    request_sync_cancel,
    run_full_sync,
)
from ..data.legacy_reuse import get_legacy_reuse_status
from ..database.connection import connect, search_symbols

router = APIRouter(prefix="/api/data", tags=["data"])
_cfg = get_config()

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.get("/status")
def data_status():
    """Data freshness summary for UI status bar."""
    return get_data_status(_cfg.db_path)


@router.get("/sync/status")
def full_sync_status():
    """Status of the shared background synchronization job."""
    return get_sync_status(_cfg.db_path)


@router.post("/sync/start")
def start_full_sync():
    """Start a full sync without blocking the API request."""
    config = get_config()
    status = get_sync_status(config.db_path)
    if status["running"]:
        return {"started": False, **status}
    threading.Thread(
        target=run_full_sync,
        args=(config,),
        daemon=True,
        name="api-full-data-sync",
    ).start()
    return {"started": True, **status}


@router.post("/sync/cancel")
def cancel_full_sync():
    """Cooperatively stop the active job at its next safe checkpoint."""
    requested = request_sync_cancel(get_config().db_path)
    return {"cancel_requested": requested}


@router.get("/health")
def db_health():
    """Comprehensive DB coverage report for Data Status panel."""
    return get_db_health(_cfg.db_path)


@router.get("/legacy-reuse")
def legacy_reuse_status():
    """Show what was reused and what still requires official evidence."""
    return get_legacy_reuse_status(_cfg.db_path)


@router.get("/missing/prices")
def missing_prices():
    """Symbols missing recent price data."""
    symbols = detect_missing_prices(_cfg.db_path)
    return {"count": len(symbols), "symbols": symbols}


@router.get("/missing/financials")
def missing_financials(year: int | None = None):
    """Symbols missing financial ratio data."""
    gaps = detect_missing_financials(_cfg.db_path, year)
    return {"count": len(gaps), "gaps": gaps}


class UpdateRequest(BaseModel):
    symbols: list[str] | None = None
    count_back: int = 30


@router.post("/update/prices")
def trigger_price_update(req: UpdateRequest):
    """The shared sync is the only supported price writer."""
    raise HTTPException(
        status_code=410,
        detail=(
            "Endpoint cập nhật giá riêng lẻ đã bị vô hiệu hóa. "
            "Hãy dùng POST /api/data/sync/start."
        ),
    )


@router.get("/update/prices/stream")
def stream_price_update(count_back: int = 30):
    """Stream the shared atomic sync instead of a second price writer."""

    def generate():
        try:
            config = get_config()
            status = get_sync_status(config.db_path)
            if not status["running"]:
                threading.Thread(
                    target=run_full_sync,
                    args=(config,),
                    daemon=True,
                    name="api-shared-price-sync",
                ).start()
            yield _sse({"type": "start", "total": 0, "symbols": []})
            for _ in range(3_600):
                status = get_sync_status(config.db_path)
                run = status.get("last_run") or {}
                if status["running"]:
                    yield _sse({
                        "type": "progress",
                        "symbol": "",
                        "index": run.get("prices_processed", 0),
                        "total": run.get("price_symbols_total", 0),
                        "status": run.get("stage"),
                        "bars": run.get("prices_updated", 0),
                        "updated": run.get("prices_updated", 0),
                        "failed": run.get("prices_failed", 0),
                        "inserted": run.get("prices_updated", 0),
                    })
                    time.sleep(2)
                    continue
                yield _sse({
                    "type": "done",
                    "remaining_missing": (
                        0 if run.get("status") == "completed" else -1
                    ),
                    "updated": run.get("prices_updated", 0),
                    "failed": run.get("prices_failed", 0),
                    "inserted": run.get("prices_updated", 0),
                    "run_status": run.get("status"),
                    "message": run.get("message"),
                    "broad_price_date": status.get("broad_price_date"),
                    "latest_market_date": status.get("latest_market_date"),
                    "provisional_prices": status.get("provisional_prices"),
                })
                return
            yield _sse({
                "type": "done",
                "remaining_missing": -1,
                "run_status": "failed",
                "message": "Không nhận được trạng thái hoàn tất từ tác vụ đồng bộ.",
            })
        except Exception as e:
            log.error("Price stream failed: %s", e)
            yield _sse({"type": "error", "message": "Internal update error"})
            yield _sse({
                "type": "done",
                "remaining_missing": -1,
                "run_status": "failed",
                "message": "Cập nhật giá gặp lỗi máy chủ.",
            })

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers=_SSE_HEADERS,
    )


@router.get("/update/financials/stream")
def stream_financials_update(year: int | None = None):
    """Stream the shared atomic refresh; never write ratios per symbol."""

    def generate():
        try:
            config = get_config()
            status = get_sync_status(config.db_path)
            if not status["running"]:
                threading.Thread(
                    target=run_full_sync,
                    args=(config,),
                    daemon=True,
                    name="api-atomic-financial-sync",
                ).start()
            yield _sse({"type": "start", "total": 0, "symbols": []})
            for _ in range(3_600):
                status = get_sync_status(config.db_path)
                run = status.get("last_run") or {}
                if status["running"]:
                    yield _sse({
                        "type": "progress",
                        "total": run.get("financial_symbols_total", 0),
                        "index": run.get("financials_updated", 0),
                        "status": run.get("stage"),
                        "rows": run.get("financial_rows_staged", 0),
                        "source": "VCI",
                        "updated": run.get("financials_updated", 0),
                        "failed": run.get("financials_failed", 0),
                        "inserted": run.get("financial_rows_staged", 0),
                    })
                    time.sleep(2)
                    continue
                yield _sse({
                    "type": "done",
                    "remaining_missing": (
                        0 if run.get("status") == "completed" else -1
                    ),
                    "updated": run.get("financials_updated", 0),
                    "failed": run.get("financials_failed", 0),
                    "inserted": run.get("financial_rows_staged", 0),
                    "run_status": run.get("status"),
                    "message": run.get("message"),
                    "broad_price_date": status.get("broad_price_date"),
                    "latest_market_date": status.get("latest_market_date"),
                    "provisional_prices": status.get("provisional_prices"),
                })
                return
            yield _sse({
                "type": "done",
                "remaining_missing": -1,
                "run_status": "failed",
                "message": "Không nhận được trạng thái hoàn tất từ tác vụ đồng bộ.",
            })
        except Exception as e:
            log.error("Financials stream failed: %s", e)
            yield _sse({"type": "error", "message": "Internal update error"})
            yield _sse({
                "type": "done",
                "remaining_missing": -1,
                "run_status": "failed",
                "message": "Cập nhật tài chính gặp lỗi máy chủ.",
            })

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers=_SSE_HEADERS,
    )


@router.get("/search")
def search(q: str = "", limit: int = 20):
    """Search symbols by ticker or company name."""
    if not q.strip():
        raise HTTPException(400, "Query parameter 'q' required")
    limit = max(1, min(limit, 100))
    with connect(_cfg.db_path) as conn:
        results = search_symbols(conn, q, limit)
    return results


def _sse(data: dict) -> str:
    """Format dict as SSE data line."""
    return f"data: {json.dumps(data)}\n\n"
