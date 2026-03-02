# Plan: Fix Data Update Bugs (260302)

> Fixes 4 critical bugs: missing financials update, silent price skips, no VCI-to-KBS fallback, poor reporting.

## Bug Summary

| # | Bug | Root Cause | Impact |
|---|-----|-----------|--------|
| 1 | Financial ratios never updated | No `update_financials()` fn, no endpoint, no frontend button | Strategy uses stale EPS data |
| 2 | Price data silently skipped | VCI returns 0 bars -> `status="skip"` with no reason; symbols with ZERO rows excluded from `detect_missing_prices()` | User gets no info on failures |
| 3 | No VCI-to-KBS fallback | KBS only used in `verifier.py`; never in `updater.py` | Single point of failure |
| 4 | Poor completion reporting | Frontend shows generic "No new data from VCI" | User can't diagnose issues |

## Files to Modify

| File | Changes |
|------|---------|
| `backend/data/updater.py` | Add `update_financials_stream()`, add skip reasons, add KBS fallback for prices, fix `detect_missing_prices()` |
| `backend/data/kbs_client.py` | Add `get_ohlcv()` method for price fallback |
| `backend/api/data_routes.py` | Add `/api/data/update/financials/stream` endpoint |
| `frontend/src/lib/api.ts` | Add `streamFinancialsUpdate()` method, add `skip_reason` to `StreamProgress` |
| `frontend/src/app/data/page.tsx` | Add "Update Financials" button, show skip reasons, improve summary |

## Detailed Changes

---

### 1. `backend/data/kbs_client.py` — Add `get_ohlcv()` Method

KBS has a price data API at `kbsv-stock-data-store/stock/ohlc-chart` (same domain as finance-info). Add price fetch capability so it can serve as fallback.

**Add at top (after existing `_FINANCE_URL`):**

```python
_OHLC_URL = "https://kbbuddywts.kbsec.com.vn/sas/kbsv-stock-data-store/stock/ohlc-chart"
```

**Add method to `KBSClient` class (after `get_financial_summary`):**

```python
def get_ohlcv(self, symbol: str, count_back: int = 30) -> list[dict[str, Any]]:
    """Fetch recent OHLCV bars from KBS. Returns same format as VCIClient."""
    sym = symbol.upper()
    params = {
        "symbol": sym,
        "timeFrame": "D",
        "count": count_back,
    }
    data = self._get(_OHLC_URL, params)
    # KBS returns { "data": [{ "tradingDate": "...", "open": ..., ... }] }
    items = data.get("data", [])
    if not items or not isinstance(items, list):
        return []
    bars: list[dict[str, Any]] = []
    for item in items:
        td = item.get("tradingDate", "")
        # tradingDate format: "2025-02-28T00:00:00" or "YYYY-MM-DD"
        date_str = td[:10] if td else None
        if not date_str:
            continue
        bars.append({
            "time": date_str,
            "open": item.get("open", 0),
            "high": item.get("high", 0),
            "low": item.get("low", 0),
            "close": item.get("close", 0),
            "volume": item.get("volume", 0),
        })
    return bars
```

**Note:** Need to add `from typing import Any` to imports (it's not currently imported).

**Important:** Before implementing, test the actual KBS OHLC endpoint format with a curl request to confirm field names. The above is based on common KBS API patterns. If the endpoint differs, adapt accordingly.

---

### 2. `backend/data/updater.py` — Core Changes

#### 2a. Fix `detect_missing_prices()` to Include Symbols with ZERO Rows

**Current problem (line 76-94):** Query only finds symbols that HAVE rows in `stock_price_history` with stale dates. Symbols with ZERO price rows are never returned.

**Replace the SQL query in `detect_missing_prices()` (lines 75-94):**

```python
rows = fetch_all(
    conn,
    """
    SELECT s.ticker
    FROM stocks s
    JOIN stock_exchange se ON se.ticker = s.ticker
    LEFT JOIN (
        SELECT symbol, MAX(time) AS latest
        FROM stock_price_history
        GROUP BY symbol
    ) sub ON sub.symbol = s.ticker
    WHERE LENGTH(s.ticker) = 3
      AND s.ticker GLOB '[A-Z][A-Z][A-Z]'
      AND se.exchange IN ('HSX', 'HNX', 'UPCOM')
      AND (
          sub.latest IS NULL          -- no price rows at all
          OR sub.latest < ?           -- behind by gap_threshold
      )
    ORDER BY s.ticker
    """,
    (gap_threshold,),
)
```

Key change: `LEFT JOIN` instead of inner subquery; `sub.latest IS NULL` catches symbols with zero rows. Remove the lower bound filter (`AND sub.latest >= ?`) since symbols with no data should always be included.

#### 2b. Add Skip Reason to `SymbolProgress`

**Modify `SymbolProgress` dataclass (line 171-183):**

Add a `skip_reason` field:

```python
@dataclass
class SymbolProgress:
    """Progress event emitted per symbol during streaming update."""
    symbol: str
    index: int
    total: int
    status: str  # "ok", "skip", "error"
    bars_inserted: int
    error: str | None
    skip_reason: str | None  # NEW: why was this symbol skipped
    # running totals
    updated_so_far: int
    failed_so_far: int
    inserted_so_far: int
```

#### 2c. Add KBS Fallback to `update_prices_stream()`

**Modify function signature (line 186-191) to accept optional KBS client:**

```python
def update_prices_stream(
    db_path: Path,
    symbols: list[str],
    vci: VCIClient,
    kbs: Optional[Any] = None,  # KBSClient, optional fallback
    count_back: int = 30,
) -> Iterator[SymbolProgress]:
```

**Modify the inner loop (lines 196-242):**

Replace the current `if not bars:` skip block with KBS fallback logic:

```python
for idx, sym in enumerate(symbols):
    sym_bars = 0
    skip_reason = None
    source = "VCI"
    try:
        bars = vci.get_ohlcv(sym, count_back=count_back)
        if not bars and kbs is not None:
            # Fallback to KBS
            try:
                bars = kbs.get_ohlcv(sym, count_back=count_back)
                source = "KBS"
            except Exception:
                bars = []
        if not bars:
            skip_reason = f"both VCI and KBS returned 0 bars" if kbs else "VCI returned 0 bars"
            yield SymbolProgress(
                symbol=sym, index=idx, total=total, status="skip",
                bars_inserted=0, error=None, skip_reason=skip_reason,
                updated_so_far=updated, failed_so_far=failed,
                inserted_so_far=inserted,
            )
            continue
        with connect_rw(db_path) as conn:
            for bar in bars:
                ts = bar.get("time")
                if ts is None:
                    continue
                date_str = _ts_to_date(ts) if source == "VCI" else ts  # KBS already returns date strings
                if date_str is None:
                    continue
                cur = conn.execute(
                    """INSERT OR IGNORE INTO stock_price_history
                    (symbol, time, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (sym, date_str, bar["open"], bar["high"],
                     bar["low"], bar["close"], bar["volume"]),
                )
                sym_bars += cur.rowcount
        inserted += sym_bars
        if sym_bars > 0:
            updated += 1
            status = "ok"
            skip_reason = None
        else:
            status = "skip"
            skip_reason = f"{source} returned {len(bars)} bars but all already existed"
        yield SymbolProgress(
            symbol=sym, index=idx, total=total, status=status,
            bars_inserted=sym_bars, error=None, skip_reason=skip_reason,
            updated_so_far=updated, failed_so_far=failed,
            inserted_so_far=inserted,
        )
    except Exception as e:
        failed += 1
        err_msg = f"{sym}: {e}"
        log.warning("Failed to update prices for %s: %s", sym, e)
        yield SymbolProgress(
            symbol=sym, index=idx, total=total, status="error",
            bars_inserted=0, error=err_msg, skip_reason=None,
            updated_so_far=updated, failed_so_far=failed,
            inserted_so_far=inserted,
        )
```

#### 2d. Add `update_financials_stream()` Function

**Add new import at top of file:**

```python
from .kbs_client import KBSClient
```

**Add new dataclass after `SymbolProgress`:**

```python
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
```

**Add the function (after `update_prices_stream`):**

```python
def update_financials_stream(
    db_path: Path,
    symbols: list[str],
    vci: VCIClient,
    kbs: Optional[KBSClient] = None,
) -> Iterator[FinancialProgress]:
    """Fetch and insert financial ratios for symbols, yielding progress per symbol."""
    import json as _json
    updated, failed, inserted = 0, 0, 0
    total = len(symbols)

    for idx, sym in enumerate(symbols):
        sym_inserted = 0
        source = "VCI"
        skip_reason = None
        try:
            rows = vci.get_annual_ratios(sym)
            if not rows and kbs is not None:
                # Fallback: KBS returns latest year only
                try:
                    kbs_row = kbs.get_financial_summary(sym)
                    rows = [_kbs_to_vci_row(kbs_row)]
                    source = "KBS"
                except Exception:
                    rows = []

            if not rows:
                skip_reason = "both VCI and KBS returned no data" if kbs else "VCI returned no data"
                yield FinancialProgress(
                    symbol=sym, index=idx, total=total, status="skip",
                    rows_inserted=0, source=source, error=None,
                    skip_reason=skip_reason,
                    updated_so_far=updated, failed_so_far=failed,
                    inserted_so_far=inserted,
                )
                continue

            with connect_rw(db_path) as conn:
                for row in rows:
                    period = f"{row.year}" if row.quarter is None else f"{row.year}-Q{row.quarter}"
                    data_dict = {
                        "price_to_book": _to_float(row.pb),
                        "price_to_earnings": _to_float(row.pe),
                        "eps_vnd": _to_float(row.eps),
                        "bvps_vnd": _to_float(row.bvps),
                        "roe": _to_float(row.roe),
                        "market_cap_billions": _to_float(getattr(row, "ev", None)),
                        "shares_outstanding_millions": _to_float(row.issue_share),
                        "year": row.year,
                        "quarter": row.quarter,
                        "period": period,
                    }
                    # Filter out None values for data_json
                    data_json = _json.dumps(
                        {k: v for k, v in data_dict.items() if v is not None}
                    )
                    cur = conn.execute(
                        """INSERT OR IGNORE INTO financial_ratios
                        (symbol, period, year, quarter,
                         price_to_book, price_to_earnings, eps_vnd, bvps_vnd,
                         roe, market_cap_billions, shares_outstanding_millions,
                         data_json, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (sym, period, row.year, row.quarter,
                         _to_float(row.pb), _to_float(row.pe),
                         _to_float(row.eps), _to_float(row.bvps),
                         _to_float(row.roe),
                         _to_float(getattr(row, "ev", None)),
                         _to_float(row.issue_share),
                         data_json, source),
                    )
                    sym_inserted += cur.rowcount

            inserted += sym_inserted
            if sym_inserted > 0:
                updated += 1
                status = "ok"
            else:
                status = "skip"
                skip_reason = f"{source} returned {len(rows)} rows but all already existed"

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
```

**Add helper functions (at bottom of file):**

```python
def _to_float(val) -> float | None:
    """Convert Decimal or numeric to float for SQLite insertion."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _kbs_to_vci_row(kbs_row):
    """Adapt KBSFinancialRow to duck-type compatible with VCIFinancialRow fields."""
    from .vci_client import VCIFinancialRow
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
```

**File size concern:** `updater.py` is currently 378 lines. Adding ~120 lines of financials streaming pushes it beyond 200. Consider extracting `update_financials_stream` + helpers into a new `backend/data/financial_updater.py` module and importing it from `updater.py` for re-export. Alternatively, keep it in `updater.py` since the functions are tightly coupled to the same DB/client dependencies. Given the 200-line rule, **extract to `backend/data/financial_updater.py`**.

**Revised approach: Create `backend/data/financial_updater.py`** containing:
- `FinancialProgress` dataclass
- `update_financials_stream()` function
- `_to_float()` helper
- `_kbs_to_vci_row()` helper

Then update `backend/data/updater.py` to re-export:
```python
from .financial_updater import FinancialProgress, update_financials_stream
```

And also move the `_to_float` helper so price updater can use it if needed.

---

### 3. `backend/api/data_routes.py` — Add Financials Stream Endpoint

**Add imports (line 11-18):**

```python
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
from ..data.kbs_client import KBSClient  # NEW
```

**Modify `stream_price_update` (line 78-102) to pass KBS client:**

In the `generate()` inner function, create KBS client alongside VCI:

```python
@router.get("/update/prices/stream")
def stream_price_update(count_back: int = 30):
    """SSE endpoint — streams per-symbol progress as JSON events."""
    symbols = detect_missing_prices(_cfg.db_path)

    def generate():
        if not symbols:
            yield f"data: {json.dumps({'type': 'done', 'total': 0, 'updated': 0, 'failed': 0, 'inserted': 0})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'start', 'total': len(symbols), 'symbols': symbols[:20]})}\n\n"

        with VCIClient(rate_limit_rpm=_cfg.vci.rate_limit_rpm) as vci, \
             KBSClient(rate_limit_rpm=_cfg.kbs.rate_limit_rpm) as kbs:
            for prog in update_prices_stream(_cfg.db_path, symbols, vci, kbs, count_back):
                yield f"data: {json.dumps({
                    'type': 'progress', 'symbol': prog.symbol,
                    'index': prog.index, 'total': prog.total,
                    'status': prog.status, 'bars': prog.bars_inserted,
                    'error': prog.error, 'skip_reason': prog.skip_reason,
                    'updated': prog.updated_so_far,
                    'failed': prog.failed_so_far,
                    'inserted': prog.inserted_so_far,
                })}\n\n"

        remaining = len(detect_missing_prices(_cfg.db_path))
        yield f"data: {json.dumps({'type': 'done', 'remaining_missing': remaining})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
```

**Add new endpoint for financials streaming (after `stream_price_update`):**

```python
@router.get("/update/financials/stream")
def stream_financials_update(year: int | None = None):
    """SSE endpoint — streams per-symbol financial ratios update progress."""
    gaps = detect_missing_financials(_cfg.db_path, year)
    symbols = [g["ticker"] for g in gaps]

    def generate():
        if not symbols:
            yield f"data: {json.dumps({'type': 'done', 'total': 0, 'updated': 0, 'failed': 0, 'inserted': 0})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'start', 'total': len(symbols), 'symbols': symbols[:20]})}\n\n"

        with VCIClient(rate_limit_rpm=_cfg.vci.rate_limit_rpm) as vci, \
             KBSClient(rate_limit_rpm=_cfg.kbs.rate_limit_rpm) as kbs:
            for prog in update_financials_stream(_cfg.db_path, symbols, vci, kbs):
                yield f"data: {json.dumps({
                    'type': 'progress', 'symbol': prog.symbol,
                    'index': prog.index, 'total': prog.total,
                    'status': prog.status, 'rows': prog.rows_inserted,
                    'source': prog.source, 'error': prog.error,
                    'skip_reason': prog.skip_reason,
                    'updated': prog.updated_so_far,
                    'failed': prog.failed_so_far,
                    'inserted': prog.inserted_so_far,
                })}\n\n"

        remaining = len(detect_missing_financials(_cfg.db_path, year))
        yield f"data: {json.dumps({'type': 'done', 'remaining_missing': remaining})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
```

**File size check:** `data_routes.py` is currently 113 lines. Adding ~40 lines for the new endpoint = ~153 lines. Under 200, acceptable.

---

### 4. `frontend/src/lib/api.ts` — Add Financials Streaming

**Extend `StreamProgress` interface (line 146-159) with skip_reason + financials fields:**

```typescript
export interface StreamProgress {
  type: "start" | "progress" | "done";
  symbol?: string;
  index?: number;
  total?: number;
  status?: string;
  bars?: number;        // price update
  rows?: number;        // financials update
  source?: string;      // "VCI" or "KBS"
  error?: string | null;
  skip_reason?: string | null;  // NEW
  updated?: number;
  failed?: number;
  inserted?: number;
  symbols?: string[];
  remaining_missing?: number;
}
```

**Add `streamFinancialsUpdate` function (after `streamPriceUpdate`):**

```typescript
function streamFinancialsUpdate(
  onEvent: (ev: StreamProgress) => void,
  onDone: () => void,
  year?: number,
): AbortController {
  const ctrl = new AbortController();
  const url = new URL("/api/data/update/financials/stream", API_BASE);
  if (year) url.searchParams.set("year", String(year));

  fetch(url.toString(), { signal: ctrl.signal })
    .then(async (res) => {
      if (!res.ok || !res.body) {
        onEvent({ type: "done", updated: 0, failed: 0, inserted: 0 });
        onDone();
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (trimmed.startsWith("data: ")) {
            try {
              const data = JSON.parse(trimmed.slice(6)) as StreamProgress;
              onEvent(data);
            } catch { /* skip malformed */ }
          }
        }
      }
      onDone();
    })
    .catch(() => {
      onDone();
    });

  return ctrl;
}
```

**DRY concern:** `streamPriceUpdate` and `streamFinancialsUpdate` share ~90% of SSE reading logic. Extract a shared `_streamSSE(url, onEvent, onDone)` helper:

```typescript
function _streamSSE(
  url: string,
  onEvent: (ev: StreamProgress) => void,
  onDone: () => void,
): AbortController {
  const ctrl = new AbortController();

  fetch(url, { signal: ctrl.signal })
    .then(async (res) => {
      if (!res.ok || !res.body) {
        onEvent({ type: "done", updated: 0, failed: 0, inserted: 0 });
        onDone();
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (trimmed.startsWith("data: ")) {
            try {
              onEvent(JSON.parse(trimmed.slice(6)) as StreamProgress);
            } catch { /* skip malformed */ }
          }
        }
      }
      onDone();
    })
    .catch(() => {
      onDone();
    });

  return ctrl;
}
```

Then simplify both:

```typescript
function streamPriceUpdate(onEvent: (ev: StreamProgress) => void, onDone: () => void): AbortController {
  return _streamSSE(new URL("/api/data/update/prices/stream", API_BASE).toString(), onEvent, onDone);
}

function streamFinancialsUpdate(
  onEvent: (ev: StreamProgress) => void, onDone: () => void, year?: number,
): AbortController {
  const url = new URL("/api/data/update/financials/stream", API_BASE);
  if (year) url.searchParams.set("year", String(year));
  return _streamSSE(url.toString(), onEvent, onDone);
}
```

**Add to `api` object:**

```typescript
export const api = {
  // ... existing methods ...
  streamFinancialsUpdate: streamFinancialsUpdate,
};
```

---

### 5. `frontend/src/app/data/page.tsx` — UI Changes

#### 5a. Add "Update Financials" Button

**Add state for financials progress (near line 59):**

```typescript
const [finProgress, setFinProgress] = useState<UpdateProgress>(INITIAL_PROGRESS);
const finAbortRef = useRef<AbortController | null>(null);
const finLogEndRef = useRef<HTMLDivElement>(null);
```

**Add `handleFinancialsUpdate()` function (after `handleUpdate`):**

Same pattern as `handleUpdate` but uses `api.streamFinancialsUpdate`. Replace `bars` with `rows` in log entries.

```typescript
function handleFinancialsUpdate() {
  if (finProgress.phase === "running") {
    finAbortRef.current?.abort();
    setFinProgress((p) => ({ ...p, phase: "done" }));
    return;
  }

  setFinProgress({ ...INITIAL_PROGRESS, phase: "running" });

  const ctrl = api.streamFinancialsUpdate(
    (ev: StreamProgress) => {
      if (ev.type === "start") {
        setFinProgress((p) => ({ ...p, total: ev.total ?? 0 }));
      } else if (ev.type === "progress") {
        setFinProgress((p) => ({
          ...p,
          current: (ev.index ?? 0) + 1,
          currentSymbol: ev.symbol ?? "",
          updated: ev.updated ?? p.updated,
          failed: ev.failed ?? p.failed,
          inserted: ev.inserted ?? p.inserted,
          log: [...p.log, {
            symbol: ev.symbol ?? "",
            status: ev.status ?? "ok",
            bars: ev.rows ?? 0,  // reuse bars field for rows count
            error: ev.error ?? undefined,
            skipReason: ev.skip_reason ?? undefined,
          }],
        }));
      } else if (ev.type === "done") {
        setFinProgress((p) => ({
          ...p, phase: "done",
          remainingMissing: ev.remaining_missing,
        }));
      }
    },
    () => {
      setFinProgress((p) => ({ ...p, phase: p.phase === "running" ? "done" : p.phase }));
      loadHealth();
    },
  );
  finAbortRef.current = ctrl;
}
```

#### 5b. Update `UpdateProgress` Interface

Add `skipReason` to the log entry type:

```typescript
interface UpdateProgress {
  phase: "idle" | "running" | "done";
  total: number;
  current: number;
  currentSymbol: string;
  updated: number;
  failed: number;
  inserted: number;
  remainingMissing?: number;
  log: { symbol: string; status: string; bars: number; error?: string; skipReason?: string }[];
}
```

#### 5c. Show Skip Reasons in Log

Update the log entry rendering (around line 222-239). Where it currently says `"no data"` for skip status, show the actual skip reason:

```tsx
{entry.status === "skip" && (
  <span className="text-gray-400">{entry.skipReason || "no new data"}</span>
)}
```

#### 5d. Add Buttons to Header

Replace the single "Update Missing Data" button (line 166-174) with two buttons:

```tsx
<div className="flex items-center gap-2">
  <button onClick={() => { setLoading(true); loadHealth(); }}
    disabled={progress.phase === "running" || finProgress.phase === "running"}
    className="px-4 py-2 bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg text-sm font-medium transition-colors disabled:opacity-50">
    Refresh
  </button>
  <button onClick={handleUpdate}
    disabled={finProgress.phase === "running"}
    className={`px-4 py-2 rounded-lg font-medium text-sm transition-colors ${
      progress.phase === "running"
        ? "bg-red-600 hover:bg-red-700 text-white"
        : "bg-blue-600 hover:bg-blue-700 text-white"
    } disabled:opacity-50`}>
    {progress.phase === "running" ? "Cancel" : "Update Prices"}
  </button>
  <button onClick={handleFinancialsUpdate}
    disabled={progress.phase === "running"}
    className={`px-4 py-2 rounded-lg font-medium text-sm transition-colors ${
      finProgress.phase === "running"
        ? "bg-red-600 hover:bg-red-700 text-white"
        : "bg-emerald-600 hover:bg-emerald-700 text-white"
    } disabled:opacity-50`}>
    {finProgress.phase === "running" ? "Cancel" : "Update Financials"}
  </button>
</div>
```

#### 5e. Improve Completion Summary

Update the "done" summary block (line 246-275). Replace generic message with detail:

```tsx
{progress.phase === "done" && progress.total > 0 && (
  <div className={`border rounded-lg p-3 text-sm ${
    progress.updated > 0
      ? "bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800 text-green-700 dark:text-green-400"
      : "bg-yellow-50 dark:bg-yellow-900/20 border-yellow-200 dark:border-yellow-800 text-yellow-700 dark:text-yellow-400"
  }`}>
    <p className="font-medium">
      {progress.updated > 0
        ? `Done! ${progress.updated} symbols updated, ${progress.inserted.toLocaleString()} rows inserted.`
        : `Done! All ${progress.total} symbols already up to date (data exists in DB).`}
    </p>
    {progress.failed > 0 && (
      <p className="text-red-600 dark:text-red-400 mt-1">
        {progress.failed} failed — check log for details.
      </p>
    )}
    {(() => {
      const skipped = progress.log.filter(e => e.status === "skip").length;
      return skipped > 0 ? (
        <p className="text-gray-500 dark:text-gray-400 mt-1">
          {skipped} skipped (source returned no data or already existed).
        </p>
      ) : null;
    })()}
    {progress.remainingMissing != null && progress.remainingMissing > 0 && (
      <p className="text-gray-500 dark:text-gray-400 mt-1">
        {progress.remainingMissing} symbols still behind.
      </p>
    )}
    {progress.remainingMissing === 0 && (
      <p className="mt-1">All data is now up to date!</p>
    )}
  </div>
)}
```

#### 5f. Add Financials Progress Panel

Add a second progress panel below the price progress panel (duplicate the `{progress.phase !== "idle" && (...)}` block but for `finProgress`). Use "Rows" label instead of "Bars". Differentiate with a green accent instead of blue.

**File size concern:** `data/page.tsx` is currently 390 lines. Adding financials progress UI will push it to ~500+. **Extract the progress panel into a component.**

Create `frontend/src/app/data/update-progress-panel.tsx`:

```typescript
"use client";

import { useEffect, useRef } from "react";
import { StreamProgress } from "@/lib/api";

interface LogEntry {
  symbol: string;
  status: string;
  bars: number;
  error?: string;
  skipReason?: string;
}

interface UpdateProgress {
  phase: "idle" | "running" | "done";
  total: number;
  current: number;
  currentSymbol: string;
  updated: number;
  failed: number;
  inserted: number;
  remainingMissing?: number;
  log: LogEntry[];
}

interface Props {
  progress: UpdateProgress;
  label: string;          // "prices" or "financials"
  unitLabel: string;      // "Bars" or "Rows"
  accentColor: string;    // "blue" or "emerald"
}

export { type UpdateProgress, type LogEntry };

export function UpdateProgressPanel({ progress, label, unitLabel, accentColor }: Props) {
  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [progress.log.length]);

  if (progress.phase === "idle") return null;

  const pct = progress.total > 0 ? (progress.current / progress.total) * 100 : 0;
  const barColor = progress.phase === "done" ? "bg-green-500" : `bg-${accentColor}-500`;

  return (
    <div className="mt-4 space-y-3">
      {/* ... condensed: same structure as existing panel ... */}
      {/* Progress bar, live counters, log, done summary */}
    </div>
  );
}
```

This keeps each file under 200 lines. The main `page.tsx` imports and renders `<UpdateProgressPanel>` twice.

---

## Implementation Order

1. **`backend/data/kbs_client.py`** — Add `get_ohlcv()` (+15 lines, test with curl first)
2. **`backend/data/updater.py`** — Fix `detect_missing_prices()` SQL, add `skip_reason` to `SymbolProgress`, add KBS param to `update_prices_stream()` (+30 lines net)
3. **`backend/data/financial_updater.py`** — NEW file: `FinancialProgress`, `update_financials_stream()`, helpers (~120 lines)
4. **`backend/api/data_routes.py`** — Add KBS import, pass KBS to price stream, add `/update/financials/stream` endpoint (+40 lines)
5. **`frontend/src/lib/api.ts`** — Extract `_streamSSE`, add `streamFinancialsUpdate`, extend types (+30 lines net)
6. **`frontend/src/app/data/update-progress-panel.tsx`** — NEW file: extracted progress panel component (~120 lines)
7. **`frontend/src/app/data/page.tsx`** — Add financials button + state, use `UpdateProgressPanel`, show skip reasons

## Testing Checklist

- [ ] `detect_missing_prices()` returns symbols with ZERO price rows
- [ ] Price update SSE includes `skip_reason` in progress events
- [ ] VCI failure falls back to KBS for price data
- [ ] `/api/data/update/financials/stream` returns SSE events
- [ ] Financial ratios inserted with correct column mapping (eps_vnd, price_to_earnings, etc.)
- [ ] VCI financial failure falls back to KBS
- [ ] `INSERT OR IGNORE` respects UNIQUE(symbol, period, year, quarter)
- [ ] Frontend "Update Financials" button triggers stream + shows progress
- [ ] Frontend log shows skip reasons instead of generic "no data"
- [ ] Done summary shows skip count + actionable info
- [ ] Both buttons disable each other during operation (prevent concurrent updates)

## VCI-to-DB Column Mapping

| VCI Field (`VCIFinancialRow`) | DB Column (`financial_ratios`) |
|-------------------------------|-------------------------------|
| `eps` | `eps_vnd` |
| `pe` | `price_to_earnings` |
| `pb` | `price_to_book` |
| `roe` | `roe` |
| `bvps` | `bvps_vnd` |
| `revenue` | (stored in `data_json` only) |
| `net_profit` | (stored in `data_json` only) |
| `issue_share` | `shares_outstanding_millions` |
| `ev` | `market_cap_billions` (note: naming is misleading in DB) |
| — | `period` = `"{year}"` for annual, `"{year}-Q{quarter}"` for quarterly |
| — | `source` = `"VCI"` or `"KBS"` |
| — | `data_json` = JSON blob of all non-null values |

## KBS OHLC API

**Needs verification:** The KBS OHLC endpoint format needs to be confirmed before implementation. The `kbsv-stock-data-store` base path exists in config but no client method uses it for price data yet.

**Action item:** Run a test request to determine exact URL path and response format:
```bash
curl -s "https://kbbuddywts.kbsec.com.vn/sas/kbsv-stock-data-store/stock/ohlc-chart?symbol=VNM&timeFrame=D&count=5" | python -m json.tool
```

If this endpoint doesn't exist, the KBS price fallback should be omitted for now (financials fallback is the higher priority).

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| KBS OHLC endpoint doesn't exist or has different format | Medium | Make KBS fallback optional (param default None); test before implementing |
| VCI rate limit hit during bulk financials update | High | Both clients already have `_throttle()` at 30 RPM; sequential calls alternate VCI/KBS requests |
| `market_cap_billions` column misuse (stores VND not billions) | Known | Existing behavior; follow same pattern as existing data; document in code comment |
| Large number of missing financials symbols (~500+) causes long stream | Medium | Frontend already has cancel button; SSE handles this gracefully |

## Unresolved Questions

1. **KBS OHLC API format:** Does `kbsv-stock-data-store/stock/ohlc-chart` actually exist? Needs testing. If not, price fallback scope reduces to financials-only.
2. **`market_cap_billions` semantics:** Existing DB stores raw VND values (not billions) despite column name. Should we add a comment or eventually rename? For now, follow existing pattern.
3. **`detect_missing_financials` exchange filter:** Current query only checks `HSX` and `HNX` (excludes UPCOM). Is this intentional for financial data? UPCOM companies may have sparser financial reporting. Keep as-is for now.
