# Code Review Report — Update Data Missing Bug Fixes

**Date:** 2026-03-02
**Reviewer:** code-reviewer agent
**Plan:** `plans/260302-fix-data-update-missing.md`

---

## Scope

| File | Lines | Role |
|------|-------|------|
| `backend/data/updater.py` | 405 | Modified — SQL fix, skip_reason, KBS fallback |
| `backend/data/kbs_client.py` | 161 | Modified — added `get_ohlcv()` |
| `backend/api/data_routes.py` | 171 | Modified — financials stream endpoint |
| `backend/data/financial_updater.py` | 172 | Created — financial ratios stream |
| `frontend/src/lib/api.ts` | 254 | Modified — `_streamSSE`, `streamFinancialsUpdate`, types |
| `frontend/src/app/data/page.tsx` | 314 | Modified — financials button, skip reasons |
| `frontend/src/app/data/update-progress-panel.tsx` | 157 | Created — reusable progress panel |

**TypeScript typecheck:** PASS (exit 0, no errors)
**Python syntax check:** PASS (all 4 modules compile cleanly)

---

## Overall Assessment

Implementation is solid. All four bugs from the plan are fixed. The KBS fallback is properly guarded, SQL uses parameterized queries throughout, SSE format is consistent, and the frontend correctly handles all event types. No hardcoded secrets. No critical security issues found.

Three medium-priority issues and several low-priority items follow.

---

## Critical Issues

None.

---

## High Priority Findings

### H1 — SSE generator exceptions are silently swallowed by FastAPI

**File:** `backend/api/data_routes.py`, lines 87–113, 126–152
**Issue:** Both `generate()` functions are Python generators wrapped in `StreamingResponse`. If an unhandled exception escapes the generator (e.g., from `detect_missing_prices()` called again at the end of the stream, or a DB connection failure mid-stream), FastAPI will silently truncate the SSE stream without sending an `error` event. The frontend `_streamSSE` will call `onDone()` normally — the user sees a completed run with no indication of failure.

**Specific risk:** Line 112 in the price stream and line 151 in the financials stream call `detect_missing_prices()` / `detect_missing_financials()` outside any try/except. A DB error here terminates the generator silently.

**Recommended fix:**

```python
def generate():
    try:
        # ... main body ...
        remaining = len(detect_missing_prices(_cfg.db_path))
        yield _sse({"type": "done", "remaining_missing": remaining})
    except Exception as e:
        log.error("SSE generator failed: %s", e)
        yield _sse({"type": "error", "message": "Internal update error"})
```

Frontend already ignores unknown event types gracefully (the `_streamSSE` reader will parse the JSON but `mapSSEtoProgress` has no `"error"` branch — add one for robustness).

---

### H2 — KBS date string not validated before DB insertion

**File:** `backend/data/updater.py`, line 237; `backend/data/kbs_client.py`, lines 123–125
**Issue:** In `update_prices_stream()`, when `source == "KBS"`, `date_str = ts` (the raw value from `bar["time"]`). `kbs_client.get_ohlcv()` sets `date_str = td[:10]` where `td = item.get("tradingDate", "")`. This slice will silently produce a wrong/empty string if:
- `tradingDate` is shorter than 10 chars (unlikely but not validated)
- The API returns a non-date format in future

In `updater.py` the VCI path runs `_ts_to_date()` which validates via regex. The KBS path trusts the client entirely. A malformed `date_str` would be inserted into the DB as-is (SQLite has no DATE type enforcement).

**Recommended fix:** Apply `_DATE_RE.match(date_str)` validation for KBS dates too, or route KBS `date_str` through `_ts_to_date()` (it already handles proper date strings via the regex fast-path on line 32).

```python
# In update_prices_stream, replace:
date_str = ts if source == "KBS" else _ts_to_date(ts)
# With:
date_str = _ts_to_date(ts)  # handles both: date strings (KBS) and unix ints (VCI)
```

This eliminates the `source`-conditional and simplifies the logic.

---

## Medium Priority Findings

### M1 — `_extract_year_from_head` has deferred `import datetime`

**File:** `backend/data/kbs_client.py`, lines 156–161

```python
def _extract_year_from_head(data: dict) -> int:
    heads = data.get("Head", [])
    if heads:
        return int(heads[0].get("YearPeriod", 0))
    import datetime          # <-- deferred import
    return datetime.date.today().year
```

`datetime` is already available in the standard library and has zero import cost. Deferred imports inside functions are reserved for heavy or conditionally-installed packages. This one suggests the import was added as an afterthought. Move it to the top of the file.

**Also:** `_extract_year_from_head` returns `0` if `YearPeriod` is missing and `int(0)` becomes the year. `get_financial_summary()` uses this year and it will store `year=0` in the DB. Add a guard:

```python
year_val = int(heads[0].get("YearPeriod", 0))
if year_val < 2000:
    raise ValueError(f"KBS returned invalid year: {year_val}")
```

---

### M2 — `financial_updater.py` duplicates `data_dict` value computation

**File:** `backend/data/financial_updater.py`, lines 79–113

`data_dict` is built with `_to_float(row.pb)`, `_to_float(row.pe)`, etc., then serialized as `data_json`. The same `_to_float()` calls are repeated when building the SQL tuple parameters (lines 107–113). Each field is converted twice. Extract to local variables:

```python
pb = _to_float(row.pb)
pe = _to_float(row.pe)
# ...
data_json = _json.dumps({k: v for k, v in {"price_to_book": pb, ...}.items() if v is not None})
cur = conn.execute("...", (sym, period, row.year, row.quarter, pb, pe, ...))
```

Minor CPU concern, but mainly a readability/maintenance issue.

---

### M3 — Tailwind dynamic class names will be purged in production

**File:** `frontend/src/app/data/update-progress-panel.tsx`, lines 47, 85–87

```tsx
const barBg = progress.phase === "done" ? "bg-green-500" : `bg-${accentColor}-500`;
// ...
<div className={`bg-${accentColor}-50 dark:bg-${accentColor}-900/20 ...`}>
<p className={`text-${accentColor}-600 dark:text-${accentColor}-400`}>
<p className={`text-lg font-bold text-${accentColor}-700 dark:text-${accentColor}-300`}>
```

Tailwind's JIT/purge mode only keeps classes that appear as complete literal strings in source. Dynamic class construction like `` `bg-${accentColor}-500` `` will be purged at build time, causing the component to render without color in production.

The component is called with `accentColor="blue"` and `accentColor="emerald"` only. Use a lookup map instead:

```tsx
const ACCENT = {
  blue:    { bg50: "bg-blue-50 dark:bg-blue-900/20", text6: "text-blue-600 dark:text-blue-400", ... },
  emerald: { bg50: "bg-emerald-50 dark:bg-emerald-900/20", text6: "text-emerald-600 dark:text-emerald-400", ... },
} as const;
type AccentColor = keyof typeof ACCENT;
```

This is a **production correctness bug** — the panel will appear unstyled in the deployed build. However since the immediate working environment appears to be dev mode (where JIT scans source), it may go unnoticed until deployment.

---

## Low Priority Suggestions

### L1 — `updater.py` exceeds 200-line guideline

The plan explicitly noted this concern and resolved it by extracting `financial_updater.py`. However `updater.py` is now 405 lines. The `get_db_health()` function alone is 80 lines. Consider extracting `get_data_status()` and `get_db_health()` into a `db_health.py` module if future work extends this file further.

### L2 — `LogEntry.bars` is used for both bars and rows

**File:** `frontend/src/app/data/update-progress-panel.tsx`, line 8
`LogEntry.bars: number` is mapped from `ev.bars` for price updates and `ev.rows` for financials (via `mapSSEtoProgress` in `page.tsx` line 49). The field name `bars` is semantically wrong when used for financial rows. Rename to `count` or `units` to be accurate for both contexts.

### L3 — SSE `start` event sends only first 20 symbols

**File:** `backend/api/data_routes.py`, lines 93–94, 132–133
```python
yield _sse({"type": "start", "total": len(symbols), "symbols": symbols[:20]})
```
This is an intentional truncation (same pattern in both endpoints). Add a comment explaining why the truncation exists (e.g., to avoid large payloads in the SSE frame). Frontend `page.tsx` doesn't read the `symbols` array from `start` events at all, so the truncation has no functional impact — but future consumers of this API may be confused.

### L4 — `_streamSSE` error branch emits synthetic done event with zeros

**File:** `frontend/src/lib/api.ts`, lines 174–177
```typescript
if (!res.ok || !res.body) {
  onEvent({ type: "done", updated: 0, failed: 0, inserted: 0 });
  onDone();
  return;
}
```
A failed HTTP response (4xx, 5xx) is silently treated as a zero-result completion. The user sees the panel transition to "done" with 0 rows inserted, with no error message. The HTTP status/text is lost. Emit a more informative event or at minimum log the status:

```typescript
if (!res.ok || !res.body) {
  console.error("SSE stream failed:", res.status, res.statusText);
  onEvent({ type: "done", updated: 0, failed: 0, inserted: 0 });
  onDone();
  return;
}
```

### L5 — `detect_missing_prices` called twice per price update cycle

**File:** `backend/api/data_routes.py`, line 85 and line 112
`detect_missing_prices()` runs once to build the symbol list, then again after the stream to compute `remaining_missing`. Both calls open DB connections and execute multi-table JOINs. For large symbol lists this doubles the DB overhead at stream start/end. Acceptable for a low-frequency operation, but worth noting.

### L6 — No `Content-Type` assertion in `_streamSSE`

**File:** `frontend/src/lib/api.ts`, line 172
The client does not verify `res.headers.get("content-type")` is `text/event-stream`. If a proxy returns a JSON error or HTML error page, the parser will attempt to process it as SSE lines, silently discarding each line (the `try/catch` around `JSON.parse` swallows it). Low risk given the API is local, but a production hardening note.

---

## Positive Observations

- All SQL uses parameterized queries (`?` placeholders) throughout — no injection risk.
- `INSERT OR IGNORE` is correctly used for idempotent upserts on both price and financial tables.
- KBS fallback is properly isolated: inner `try/except` in `update_prices_stream` and `update_financials_stream` ensures VCI exception doesn't propagate to the outer symbol loop.
- `SymbolProgress` and `FinancialProgress` dataclasses carry full running totals per event — the frontend can reconstruct state from any single event without maintaining separate counters.
- `_streamSSE` extraction correctly DRYs the SSE reading logic (plan requirement met).
- `update-progress-panel.tsx` is cleanly parameterized for reuse with `label`, `unitLabel`, `accentColor` props.
- `_kbs_to_vci_row` adapter correctly maps KBS optional fields to `None` for `issue_share` and `ev`, avoiding silent data corruption.
- `datetime.datetime.fromtimestamp()` in `_ts_to_date` uses local timezone. For a Vietnam-market tool this is the expected behavior (VCI timestamps are in Vietnam time). No issue.
- No hardcoded credentials, API keys, or secrets anywhere in the reviewed files.
- TypeScript typecheck passes cleanly — no unsafe casts, no `any` except where intentional in the shared `StreamProgress` interface.

---

## Recommended Actions (Prioritized)

1. **[H1] Wrap SSE generator bodies in try/except**, emit `{"type":"error"}` events on failure — prevents silent stream truncation on DB errors.
2. **[H2] Route KBS date strings through `_ts_to_date()`** — eliminates the source-conditional and validates format before DB insertion.
3. **[M3] Fix dynamic Tailwind class names in `update-progress-panel.tsx`** — use a static lookup map keyed by `accentColor` to prevent production purge stripping colors.
4. **[M1] Move `import datetime` to module top in `kbs_client.py`**, add year validity guard in `_extract_year_from_head`.
5. **[M2] Deduplicate `_to_float()` calls in `financial_updater.py`** — compute once into locals, use in both `data_dict` and SQL tuple.
6. **[L2] Rename `LogEntry.bars` to `LogEntry.count`** — semantically correct for both bars and rows contexts.
7. **[L4] Log HTTP error status in `_streamSSE`** error branch to aid debugging.

---

## Plan Task Status

All tasks from `plans/260302-fix-data-update-missing.md` are implemented:

| Task | Status |
|------|--------|
| `kbs_client.py` — `get_ohlcv()` | DONE |
| `updater.py` — fix `detect_missing_prices()` SQL (LEFT JOIN + IS NULL) | DONE |
| `updater.py` — add `skip_reason` to `SymbolProgress` | DONE |
| `updater.py` — KBS fallback in `update_prices_stream()` | DONE |
| `financial_updater.py` — NEW file with `FinancialProgress` + `update_financials_stream()` | DONE |
| `data_routes.py` — `/update/financials/stream` endpoint | DONE |
| `data_routes.py` — pass KBS to price stream | DONE |
| `api.ts` — `_streamSSE` extraction + `streamFinancialsUpdate` | DONE |
| `api.ts` — `StreamProgress` type extended | DONE |
| `page.tsx` — financials button + state + `mapSSEtoProgress` helper | DONE |
| `update-progress-panel.tsx` — NEW reusable component | DONE |

**Next steps:** Address H1 (SSE error handling) and M3 (Tailwind dynamic classes) before production deployment.

---

## Unresolved Questions

1. **KBS OHLC API format confirmed?** The plan flagged this as needing a curl test before implementation. The implementation assumes `tradingDate` field and `data[]` array structure. If production reveals a different schema, `kbs_client.get_ohlcv()` will silently return `[]` (safe fallback, but defeats the purpose).
2. **`market_cap_billions` column stores raw EV (not billions VND).** Existing known issue from the plan. Should be documented in a column comment in the DB schema.
3. **`detect_missing_financials` excludes UPCOM** intentionally? Only `HSX` and `HNX` checked. UPCOM symbols will never be flagged for financial updates. Confirm this is the desired scope.
