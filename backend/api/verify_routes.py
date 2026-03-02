"""Data verification API routes — VCI vs KBS cross-check."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..config import get_config
from ..data.kbs_client import KBSClient
from ..data.vci_client import VCIClient
from ..data.verifier import verify_batch, verify_symbol

router = APIRouter(prefix="/api/verify", tags=["verify"])
_cfg = get_config()


@router.get("/{symbol}")
def verify_single(symbol: str, year: int | None = None):
    """Cross-check VCI vs KBS data for a single symbol."""
    symbol = symbol.strip().upper()
    if not symbol:
        raise HTTPException(400, "Symbol required")
    with VCIClient(_cfg.vci.rate_limit_rpm) as vci, \
         KBSClient(_cfg.kbs.rate_limit_rpm) as kbs:
        result = verify_symbol(symbol, vci, kbs, year)
    return {
        "symbol": result.symbol,
        "year": result.year,
        "overall_status": result.overall_status.value,
        "ok": result.ok_count,
        "warnings": result.warning_count,
        "errors": result.error_count,
        "comparisons": [
            {
                "metric": c.metric,
                "vci": str(c.vci_value) if c.vci_value is not None else None,
                "kbs": str(c.kbs_value) if c.kbs_value is not None else None,
                "diff_pct": c.diff_pct,
                "status": c.status.value,
                "note": c.note,
            }
            for c in result.comparisons
        ],
    }


@router.get("/batch/check")
def verify_batch_endpoint(symbols: str, year: int | None = None):
    """Batch verify multiple symbols. Pass comma-separated symbols."""
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        raise HTTPException(400, "At least one symbol required")
    if len(sym_list) > 50:
        raise HTTPException(400, "Max 50 symbols per batch")
    with VCIClient(_cfg.vci.rate_limit_rpm) as vci, \
         KBSClient(_cfg.kbs.rate_limit_rpm) as kbs:
        results = verify_batch(sym_list, vci, kbs, year)
    return [
        {
            "symbol": r.symbol,
            "year": r.year,
            "overall_status": r.overall_status.value,
            "ok": r.ok_count,
            "warnings": r.warning_count,
            "errors": r.error_count,
        }
        for r in results
    ]
