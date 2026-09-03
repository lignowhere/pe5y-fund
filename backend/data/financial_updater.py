"""Financial ratios update stream — VCI primary, KBS fallback."""
from __future__ import annotations

import json as _json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from ..database.connection import connect_rw
from .kbs_client import KBSClient
from .vci_client import VCIClient, VCIFinancialRow

log = logging.getLogger(__name__)


@dataclass
class FinancialProgress:
    """Progress event emitted per symbol during financial ratios update."""
    symbol: str
    index: int
    total: int
    status: str  # "ok", "skip", "error"
    rows_inserted: int
    source: str  # "VCI" or "KBS"
    error: str | None
    skip_reason: str | None
    # running totals
    updated_so_far: int
    failed_so_far: int
    inserted_so_far: int


def update_financials_stream(
    db_path: Path,
    symbols: list[str],
    vci: VCIClient,
    kbs: Optional[KBSClient] = None,
    target_year: Optional[int] = None,
) -> Iterator[FinancialProgress]:
    """Fetch and insert financial ratios for symbols, yielding progress.

    When target_year is set, a symbol is only counted as "ok" if the
    target year's data was actually inserted or already exists.
    """
    updated, failed, inserted = 0, 0, 0
    total = len(symbols)

    for idx, sym in enumerate(symbols):
        sym_inserted = 0
        source = "VCI"
        skip_reason = None
        has_target_year = False
        try:
            get_all = getattr(vci, "get_all_financial_ratios", None)
            rows = (
                get_all(sym)
                if callable(get_all)
                else vci.get_annual_ratios(sym) + vci.get_quarterly_ratios(sym)
            )
            if not rows and kbs is not None:
                try:
                    kbs_row = kbs.get_financial_summary(sym)
                    rows = [_kbs_to_vci_row(kbs_row)]
                    source = "KBS"
                except Exception:
                    rows = []

            if not rows:
                skip_reason = (
                    "both VCI and KBS returned no data" if kbs
                    else "VCI returned no data"
                )
                yield FinancialProgress(
                    symbol=sym, index=idx, total=total, status="skip",
                    rows_inserted=0, source=source, error=None,
                    skip_reason=skip_reason,
                    updated_so_far=updated, failed_so_far=failed,
                    inserted_so_far=inserted,
                )
                continue

            # Track which years are available from the source
            available_years = sorted({r.year for r in rows})

            with connect_rw(db_path) as conn:
                for row in rows:
                    period = (
                        f"{row.year}"
                        if row.quarter is None
                        else f"{row.year}-Q{row.quarter}"
                    )
                    pb = _to_float(row.pb)
                    pe = _to_float(row.pe)
                    eps = _to_float(row.eps)
                    bvps = _to_float(row.bvps)
                    roe = _to_float(row.roe)
                    mcap = _to_float(getattr(row, "ev", None))
                    shares = _to_float(row.issue_share)

                    data_dict = {
                        "price_to_book": pb,
                        "price_to_earnings": pe,
                        "eps_vnd": eps, "bvps_vnd": bvps,
                        "roe": roe, "market_cap_billions": mcap,
                        "shares_outstanding_millions": shares,
                        "year": row.year, "quarter": row.quarter,
                        "period": period,
                    }
                    data_json = _json.dumps(
                        {k: v for k, v in data_dict.items()
                         if v is not None}
                    )
                    cur = conn.execute(
                        """INSERT OR REPLACE INTO financial_ratios
                        (symbol, period, year, quarter,
                         price_to_book, price_to_earnings, eps_vnd,
                         bvps_vnd, roe, market_cap_billions,
                         shares_outstanding_millions, data_json,
                         source)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (sym, period, row.year, row.quarter,
                         pb, pe, eps, bvps, roe, mcap, shares,
                         data_json, source),
                    )
                    sym_inserted += cur.rowcount
                    if target_year is not None and row.year == target_year:
                        has_target_year = True

            inserted += sym_inserted

            # Determine status based on target year coverage
            if target_year is not None and not has_target_year:
                status = "skip"
                latest = max(available_years) if available_years else "?"
                skip_reason = (
                    f"no {target_year} data yet (latest: {latest})"
                )
                # Still count non-target inserts separately
                if sym_inserted > 0:
                    skip_reason += f", +{sym_inserted} older rows"
            elif sym_inserted > 0:
                updated += 1
                status = "ok"
            else:
                status = "skip"
                skip_reason = (
                    f"{source}: {len(rows)} rows but all already existed"
                )

            yield FinancialProgress(
                symbol=sym, index=idx, total=total, status=status,
                rows_inserted=sym_inserted, source=source, error=None,
                skip_reason=skip_reason,
                updated_so_far=updated, failed_so_far=failed,
                inserted_so_far=inserted,
            )
        except Exception as e:
            failed += 1
            err_msg = f"{sym}: {e}"
            log.warning("Failed to update financials for %s: %s", sym, e)
            yield FinancialProgress(
                symbol=sym, index=idx, total=total, status="error",
                rows_inserted=0, source=source, error=err_msg,
                skip_reason=None,
                updated_so_far=updated, failed_so_far=failed,
                inserted_so_far=inserted,
            )


def _to_float(val: Any) -> float | None:
    """Convert Decimal or numeric to float for SQLite insertion."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _kbs_to_vci_row(kbs_row: Any) -> VCIFinancialRow:
    """Adapt KBSFinancialRow to VCIFinancialRow for uniform processing."""
    return VCIFinancialRow(
        symbol=kbs_row.symbol,
        year=kbs_row.year,
        quarter=None,
        eps=kbs_row.eps,
        pe=kbs_row.pe,
        pb=kbs_row.pb,
        roe=kbs_row.roe,
        revenue=kbs_row.revenue,
        net_profit=kbs_row.net_profit,
        bvps=kbs_row.bvps,
        issue_share=None,
        ev=None,
    )
