"""Data management API routes — status, update triggers, search."""
from __future__ import annotations

import datetime
import json
import logging

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
    update_prices,
    update_prices_stream,
)
from ..data.financial_updater import update_financials_stream
from ..data.vci_client import VCIClient
from ..data.kbs_client import KBSClient
from ..database.connection import connect, search_symbols

router = APIRouter(prefix="/api/data", tags=["data"])
_cfg = get_config()

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.get("/status")
def data_status():
    """Data freshness summary for UI status bar."""
    return get_data_status(_cfg.db_path)


@router.get("/health")
def db_health():
    """Comprehensive DB coverage report for Data Status panel."""
    return get_db_health(_cfg.db_path)


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
    """Manually trigger price update for specified or missing symbols."""
    symbols = req.symbols
    if not symbols:
        symbols = detect_missing_prices(_cfg.db_path)
    if not symbols:
        return {"message": "No symbols need updating", "result": None}
    with VCIClient(rate_limit_rpm=_cfg.vci.rate_limit_rpm) as vci:
        result = update_prices(_cfg.db_path, symbols, vci, req.count_back)
    return {
        "message": f"Updated {result.symbols_updated}, failed {result.symbols_failed}",
        "result": {
            "updated": result.symbols_updated,
            "failed": result.symbols_failed,
            "inserted": result.prices_inserted,
            "errors": result.errors[:10],
        },
    }


@router.get("/update/prices/stream")
def stream_price_update(count_back: int = 30):
    """SSE endpoint — streams per-symbol price update progress."""
    symbols = detect_missing_prices(_cfg.db_path)

    def generate():
        try:
            if not symbols:
                yield _sse({"type": "done", "total": 0, "updated": 0,
                            "failed": 0, "inserted": 0})
                return

            yield _sse({"type": "start", "total": len(symbols),
                         "symbols": symbols[:20]})

            with VCIClient(rate_limit_rpm=_cfg.vci.rate_limit_rpm) as vci, \
                 KBSClient(rate_limit_rpm=_cfg.kbs.rate_limit_rpm) as kbs:
                for prog in update_prices_stream(
                    _cfg.db_path, symbols, vci, kbs, count_back
                ):
                    yield _sse({
                        "type": "progress", "symbol": prog.symbol,
                        "index": prog.index, "total": prog.total,
                        "status": prog.status, "bars": prog.bars_inserted,
                        "error": prog.error,
                        "skip_reason": prog.skip_reason,
                        "updated": prog.updated_so_far,
                        "failed": prog.failed_so_far,
                        "inserted": prog.inserted_so_far,
                    })

            remaining = len(detect_missing_prices(_cfg.db_path))
            yield _sse({"type": "done", "remaining_missing": remaining})
        except Exception as e:
            log.error("Price stream failed: %s", e)
            yield _sse({"type": "error", "message": "Internal update error"})
            yield _sse({"type": "done", "remaining_missing": -1})

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers=_SSE_HEADERS,
    )


@router.get("/update/financials/stream")
def stream_financials_update(year: int | None = None):
    """SSE endpoint — streams per-symbol financial ratios update progress."""
    resolved_year = year if year is not None else datetime.date.today().year - 1
    gaps = detect_missing_financials(_cfg.db_path, resolved_year)
    symbols = [g["ticker"] for g in gaps]

    def generate():
        try:
            if not symbols:
                yield _sse({"type": "done", "total": 0, "updated": 0,
                            "failed": 0, "inserted": 0})
                return

            yield _sse({"type": "start", "total": len(symbols),
                         "symbols": symbols[:20]})

            with VCIClient(rate_limit_rpm=_cfg.vci.rate_limit_rpm) as vci, \
                 KBSClient(rate_limit_rpm=_cfg.kbs.rate_limit_rpm) as kbs:
                for prog in update_financials_stream(
                    _cfg.db_path, symbols, vci, kbs,
                    target_year=resolved_year,
                ):
                    yield _sse({
                        "type": "progress", "symbol": prog.symbol,
                        "index": prog.index, "total": prog.total,
                        "status": prog.status, "rows": prog.rows_inserted,
                        "source": prog.source, "error": prog.error,
                        "skip_reason": prog.skip_reason,
                        "updated": prog.updated_so_far,
                        "failed": prog.failed_so_far,
                        "inserted": prog.inserted_so_far,
                    })

            remaining = len(detect_missing_financials(_cfg.db_path, resolved_year))
            yield _sse({"type": "done", "remaining_missing": remaining})
        except Exception as e:
            log.error("Financials stream failed: %s", e)
            yield _sse({"type": "error", "message": "Internal update error"})
            yield _sse({"type": "done", "remaining_missing": -1})

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers=_SSE_HEADERS,
    )


@router.get("/search")
def search(q: str = "", limit: int = 20):
    """Search symbols by ticker or company name."""
    if not q.strip():
        raise HTTPException(400, "Query parameter 'q' required")
    with connect(_cfg.db_path) as conn:
        results = search_symbols(conn, q, limit)
    return results


def _sse(data: dict) -> str:
    """Format dict as SSE data line."""
    return f"data: {json.dumps(data)}\n\n"
