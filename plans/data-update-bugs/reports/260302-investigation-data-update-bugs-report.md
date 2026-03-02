# Data Update Bug Investigation Report
**Date**: 260302
**Scope**: "Update Missing Data" feature — financial ratios 2025, price data, VCI→KBS fallback, reporting

---

## Executive Summary

Three independent bugs found. One architectural gap (no fallback). One UX gap (poor post-update explanation).

1. **Financial ratios 2025 MISSING after update** — Root cause: update button ONLY updates PRICE data. There is NO endpoint and NO code path to update financial ratios. Financial ratio fetching code exists in `VCIClient` but is NEVER called by the update pipeline.
2. **Price data still missing after update** — Root cause: `update_prices_stream` treats "no bars returned" as `status="skip"` (silent success), not an error. When VCI returns empty, the symbol stays missing with no explanation. Also `count_back=30` may not reach far enough for very stale symbols.
3. **No VCI→KBS fallback** — KBS is ONLY used in the `/api/verify/*` endpoints for cross-checking. It is NEVER used as a data source fallback when VCI fails or returns nothing.
4. **Reporting gaps** — After update, user sees "no data available from VCI" but no per-symbol reason for WHY. `skip` status gives zero diagnostic information.

---

## Technical Analysis

### File Map

```
backend/
  api/data_routes.py          — API endpoints for update/status/health
  api/verify_routes.py        — Verification-only; uses KBS for cross-check
  data/updater.py             — Update orchestrator (prices only)
  data/vci_client.py          — VCI API: get_ohlcv(), get_financial_ratios()
  data/kbs_client.py          — KBS API: get_financial_summary() (ratios only)
  data/verifier.py            — VCI vs KBS comparison logic (not used in update)
  scheduler/__init__.py       — Background scheduler (prices only)
frontend/src/app/data/page.tsx — Data management page
frontend/src/lib/api.ts        — Frontend API client
```

---

### Bug 1: Financial Ratios 2025 Never Updated

**Root cause**: The "Update Missing Data" button on the frontend calls ONLY `streamPriceUpdate`, which hits `GET /api/data/update/prices/stream`. This endpoint ONLY updates `stock_price_history`. There is NO endpoint for updating `financial_ratios`.

**Evidence**:

`frontend/src/app/data/page.tsx` line 92:
```typescript
const ctrl = api.streamPriceUpdate(   // <-- only price update
```

`frontend/src/lib/api.ts` line 161–201:
```typescript
function streamPriceUpdate(...)  // calls /api/data/update/prices/stream only
```

`backend/api/data_routes.py` lines 57–102: only two update endpoints exist:
- `POST /api/data/update/prices`
- `GET /api/data/update/prices/stream`

**Missing**: There is NO `POST /api/data/update/financials` or equivalent endpoint.

`backend/data/vci_client.py` lines 107–143: `VCIClient.get_financial_ratios()` and `get_annual_ratios()` EXIST but are never called from `updater.py`. They are only used in `verifier.py`.

`backend/data/updater.py`: Contains `detect_missing_financials()` (lines 98–122) but NO `update_financials()` function.

**Impact**: Financial ratios for year 2025 (check_year = `today.year - 1` = 2025) will NEVER be updated regardless of how many times the user clicks "Update Missing Data".

**Detection**: `detect_missing_financials()` at `updater.py` line 103 uses:
```python
year = datetime.date.today().year - 1  # = 2025 as of today
```
So it correctly detects 2025 as the check year — but there's no code to fix it.

---

### Bug 2: Price Data Still Missing After Update — Silent Skip

**Root cause A — VCI returns empty bars, treated as skip, not error**:

`backend/data/updater.py` lines 197–205 (`update_prices_stream`):
```python
bars = vci.get_ohlcv(sym, count_back=count_back)
if not bars:
    yield SymbolProgress(..., status="skip", ...)  # silent! no error
    continue
```

When VCI returns `[]` for a symbol (e.g. delisted, suspended, API issue), the symbol gets `status="skip"` with `bars_inserted=0`. The symbol is NOT marked as failed. The counter stays at 0. The user sees "no data" in the log but doesn't know WHY.

**Root cause B — count_back=30 may be insufficient**:

`backend/api/data_routes.py` line 79: `count_back: int = 30`

Default is 30 bars (30 trading days). If a symbol is missing more than 30 days of data (e.g. 2+ months stale), the update will succeed for the 30 most recent bars but the symbol may still appear as missing if the gap_threshold check covers a longer period.

**Root cause C — detect_missing_prices threshold logic**:

`backend/data/updater.py` lines 46–95: `detect_missing_prices` uses `min_trading_day_gap=3` and checks:
```sql
AND sub.latest < ?   -- less than 3-market-day-ago threshold
AND sub.latest >= ?  -- but only if it has SOME data (last 10 days)
```

Symbols with NO price data at all are excluded from the missing list (they don't appear in the subquery). Completely absent symbols never get added to the update queue.

**Root cause D — VCI API failure is swallowed**:

`backend/data/updater.py` lines 162–166:
```python
except Exception as e:
    failed += 1
    errors.append(f"{sym}: {e}")
    log.warning(...)
```
Only logged server-side. The frontend shows errors from the SSE stream but does not surface the actual VCI error text to the user in a meaningful way.

---

### Bug 3 (Architectural Gap): No VCI → KBS Fallback

**Answer: NO, there is NO fallback from VCI to KBS.**

KBS is used ONLY in:
- `backend/data/verifier.py` — cross-checking/comparison tool
- `backend/api/verify_routes.py` — `/api/verify/{symbol}` endpoints

KBS is NEVER used in:
- `backend/data/updater.py` — no KBS import, no fallback logic
- `backend/scheduler/__init__.py` — only imports VCIClient
- `backend/api/data_routes.py` — only imports VCIClient

`backend/data/kbs_client.py` `get_financial_summary()` returns ONLY the latest year's data (lines 79–108), not historical series. Even if a fallback were added, KBS cannot easily replace VCI for bulk historical financial data population.

---

### Gap 4: Reporting / User Feedback

**Current behavior**:
- SSE stream emits per-symbol events with `status: "ok" | "skip" | "error"` and `bars_inserted`
- Frontend (`page.tsx` lines 253–255) shows: `"Done! No new data available from VCI."` when `updated === 0`
- `skip` symbols appear in log as "no data" — no reason given
- `error` symbols show the raw error string — this is OK
- Post-update: `remainingMissing` count is shown but no breakdown of WHY they remain

**Missing feedback**:
- No per-symbol explanation of WHY skip occurred (suspended? delisted? API limit? data truly not available?)
- No financial ratios update button or progress at all
- No indication that financial ratios are NEVER updated by the "Update Missing Data" button
- The summary message "No new data available from VCI" is misleading — it doesn't clarify that financials were never attempted

---

## Root Cause Summary Table

| Issue | File | Line(s) | Root Cause |
|-------|------|---------|-----------|
| Financials never updated | `updater.py` | 98–122 | `update_financials()` function doesn't exist |
| No financials update endpoint | `data_routes.py` | all | `POST /api/data/update/financials` missing |
| Update button only triggers prices | `page.tsx` | 92 | calls `streamPriceUpdate` only |
| Skip = silent, no reason | `updater.py` | 197–205 | empty bars → skip, no VCI error surfaced |
| No VCI→KBS fallback | `updater.py` | all | KBSClient never imported in updater |
| Financials check year correct | `updater.py` | 103 | year=2025 correctly detected, never fixed |

---

## Recommendations (Prioritized)

### P1 — Add `update_financials()` to updater.py (Critical)

In `backend/data/updater.py`, add:

```python
def update_financials(
    db_path: Path,
    symbols: list[str],
    vci: VCIClient,
    year: int | None = None,
) -> UpdateResult:
    """Fetch and insert annual financial ratios for given symbols."""
    if year is None:
        year = datetime.date.today().year - 1
    updated, failed, inserted = 0, 0, 0
    errors: list[str] = []
    for sym in symbols:
        try:
            rows = vci.get_annual_ratios(sym)
            year_rows = [r for r in rows if r.year == year and r.quarter is None]
            if not year_rows:
                continue
            with connect_rw(db_path) as conn:
                sym_inserted = 0
                for row in year_rows:
                    cur = conn.execute(
                        """INSERT OR IGNORE INTO financial_ratios
                        (symbol, year, quarter, eps, pe, pb, roe, revenue, net_profit)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (row.symbol, row.year, row.quarter,
                         row.eps, row.pe, row.pb, row.roe,
                         row.revenue, row.net_profit),
                    )
                    sym_inserted += cur.rowcount
            inserted += sym_inserted
            if sym_inserted > 0:
                updated += 1
        except Exception as e:
            failed += 1
            errors.append(f"{sym}: {e}")
    return UpdateResult(updated, failed, inserted, errors)
```

Also add a streaming variant `update_financials_stream()` following the same pattern as `update_prices_stream`.

### P1 — Add `POST /api/data/update/financials` endpoint

In `backend/api/data_routes.py`, add:

```python
@router.get("/update/financials/stream")
def stream_financial_update(year: int | None = None):
    """SSE endpoint — streams per-symbol progress for financial ratio update."""
    gaps = detect_missing_financials(_cfg.db_path, year)
    symbols = [g["ticker"] for g in gaps]
    check_year = (year or datetime.date.today().year - 1)

    def generate():
        if not symbols:
            yield f"data: {json.dumps({'type': 'done', 'total': 0})}\n\n"
            return
        yield f"data: {json.dumps({'type': 'start', 'total': len(symbols), 'year': check_year})}\n\n"
        with VCIClient(rate_limit_rpm=_cfg.vci.rate_limit_rpm) as vci:
            for prog in update_financials_stream(_cfg.db_path, symbols, vci, check_year):
                yield f"data: {json.dumps({...})}\n\n"
        remaining = len(detect_missing_financials(_cfg.db_path, check_year))
        yield f"data: {json.dumps({'type': 'done', 'remaining_missing': remaining})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", ...)
```

### P1 — Frontend: Add "Update Financials" button

In `frontend/src/app/data/page.tsx`, add a second button that calls a `streamFinancialUpdate` API function, with its own progress panel. The current single button labeled "Update Missing Data" should be disambiguated to "Update Missing Prices".

### P2 — Improve "skip" reporting

In `backend/data/updater.py` lines 199–205, add a reason field:

```python
# Instead of silent skip, add a reason
skip_reason = "VCI returned no bars (possibly suspended/delisted)"
yield SymbolProgress(..., status="skip", error=skip_reason, ...)
```

Also log the skip on the server side so it appears in backend logs.

### P2 — Surface "skip" reason in frontend

In `frontend/src/app/data/page.tsx` line 231, the `skip` case shows "no data". Change to show the actual `error` field if present:

```tsx
{entry.status === "skip" && (
  <span className="text-gray-400">{entry.error || "no data from VCI"}</span>
)}
```

### P3 — Consider VCI→KBS fallback for financials

KBS `get_financial_summary()` returns only the latest year. For symbols where VCI returns empty financial data, KBS can be used as fallback. However, KBS only provides a subset of metrics (EPS, PE, PB, ROE, BVPS, revenue, net_profit — no `issue_share`, `ev`). If this is acceptable:

In `update_financials()`, after VCI returns empty for a symbol, call `kbs.get_financial_summary(sym)` and insert from KBS with a `source='KBS'` tag (requires schema change).

### P3 — Check field mapping in financial_ratios table

Before implementing `update_financials()`, verify the exact column names in the `financial_ratios` schema. The `VCIFinancialRow` has `issue_share` and `ev` fields — confirm these columns exist in the DB or adjust accordingly.

### P3 — Handle completely absent symbols in price update

`detect_missing_prices()` excludes symbols with NO price history (they never appear in `stock_price_history`). Add a separate query to detect symbols that are in `stocks`/`stock_exchange` but have ZERO rows in `stock_price_history`, and offer to bootstrap them separately.

---

## Unresolved Questions

1. What are the exact column names in the `financial_ratios` table? The schema is not in the codebase — need to check the live DB to confirm field names before writing `INSERT` for financials.
2. Does VCI's `CompanyFinancialRatio` return 2025 annual data as of March 2026, or only up to 2024? Annual reports in Vietnam are typically available 3–4 months after year-end. The VCI API may legitimately not have 2025 annual data yet (it's only March 2026). This needs testing against the actual API.
3. For KBS fallback: does KBS reliably return 2025 data for all symbols? Same seasonality concern applies.
4. The `count_back=30` in price update — is this enough for the stale symbols being reported as still missing after update? Needs investigation of the specific symbols that remain missing.
