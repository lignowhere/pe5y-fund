# Code Review Report — pe5y-fund Backend Python

**Date:** 2026-03-02
**Reviewer:** code-review agent
**Scope:** Backend Python codebase — 10 files, ~700 LOC

---

## Scope

**Files reviewed:**
- `backend/data/updater.py` (~515 lines)
- `backend/data/financial_updater.py` (~192 lines)
- `backend/data/vci_client.py` (~202 lines)
- `backend/data/kbs_client.py` (~162 lines)
- `backend/api/data_routes.py` (~187 lines)
- `backend/api/strategy_routes.py` (~118 lines)
- `backend/api/verify_routes.py` (~67 lines)
- `backend/main.py` (~59 lines)
- `backend/config.py` (~73 lines)
- `backend/database/connection.py` (~101 lines)

**Supporting files also read:** `backend/scheduler/__init__.py`, `backend/data/verifier.py`, `backend/strategy/optimizer.py`

**Syntax check:** All 10 files parse clean (Python 3.12 AST).

---

## Overall Assessment

The codebase is well-structured with clear separation between data fetching, persistence, and API layers. Error handling is present at the right boundaries. The dominant issue category is **architecture/correctness** — one critical route-shadowing bug that makes a whole endpoint unreachable, several high-severity issues around DB connection reuse and thread safety, and a cluster of medium issues around data integrity semantics and hardcoded state. Security posture is generally good; no SQL injection risk (all queries parameterized). No secrets in code.

---

## Critical Issues

### C1 — Route Shadowing: `/api/verify/batch/check` is Unreachable
**File:** `backend/api/verify_routes.py:15, 45`
**Severity:** Critical

```python
@router.get("/{symbol}")          # line 15 — registered FIRST
def verify_single(symbol: str, ...):

@router.get("/batch/check")       # line 45 — NEVER matched
def verify_batch_endpoint(symbols: str, ...):
```

Starlette (FastAPI's router) matches routes in registration order. `/{symbol}` is a catch-all path parameter that matches any single path segment, including `batch`. A `GET /api/verify/batch/check` request is captured by `verify_single` with `symbol="batch"`, which then calls the VCI/KBS API for ticker "BATCH", fails or returns garbage, and the batch endpoint is never reachable.

**Fix:** Reorder so the literal route is declared first:
```python
@router.get("/batch/check")       # literal first
@router.get("/{symbol}")          # param catch-all second
```

---

## High Priority Findings

### H1 — N+1 DB Connections Per Symbol in `update_prices()`
**File:** `backend/data/updater.py:203-234`
**Severity:** High (performance + lock contention)

```python
for sym in symbols:          # could be 700+ symbols
    bars = vci.get_ohlcv(...)
    with connect_rw(db_path) as conn:   # new connection per symbol
        for bar in bars:
            conn.execute(INSERT OR IGNORE ...)
```

Same pattern in `_insert_bars()` (line 258). Each call opens SQLite, commits, and closes. With 700 symbols this creates 700+ write transactions. In a single-writer SQLite environment with a concurrent background scheduler thread, this also maximizes lock contention window.

**Fix:** Open one connection outside the symbol loop and batch all inserts under a single transaction (or per-symbol savepoint):
```python
with connect_rw(db_path) as conn:
    for sym in symbols:
        bars = vci.get_ohlcv(...)
        for bar in bars:
            conn.execute(INSERT ...)
```

---

### H2 — Race Condition: Scheduler Thread + SSE Update Stream Write Concurrently
**File:** `backend/scheduler/__init__.py:62-68`, `backend/api/data_routes.py:102-116`
**Severity:** High (data integrity / SQLite locking)

The scheduler fires `_run_price_update` in a daemon thread 6 seconds after startup. A user-triggered SSE stream (`GET /api/data/update/prices/stream`) runs in the FastAPI async worker and calls `_insert_bars` in the same thread. Both paths call `connect_rw(db_path)` independently. SQLite in WAL mode handles this safely, but **WAL mode is never enabled** (no `PRAGMA journal_mode=WAL` anywhere in `connection.py`). Default journal mode is DELETE, which serializes all writers with exclusive locks — one path will receive `sqlite3.OperationalError: database is locked` and fail silently (scheduler swallows all exceptions at line 38; SSE stream catches and emits `{type:"error"}`).

**Fix (two parts):**
1. Add `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` in `connect_rw()`:
```python
conn = sqlite3.connect(str(db_path))
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=5000")
```
2. Consider a threading.Lock guard around scheduler writes to serialize with SSE writes.

---

### H3 — `_to_int()` Returns 0 on Failure — Corrupt Date Inserted as Epoch
**File:** `backend/data/vci_client.py:185-192`, `backend/data/updater.py:70-88`
**Severity:** High (silent data corruption)

```python
def _to_int(val: Any) -> int:
    ...
    except (TypeError, ValueError):
        return 0      # returns UNIX epoch 0 = 1970-01-01
```

`get_ohlcv()` passes VCI timestamps through `_to_int()` before building bar dicts. If a timestamp is unparseable, `_to_int` returns `0`. This then flows into `_ts_to_date(0)` → `"1970-01-01"`, and `_insert_bars` writes a bar dated 1970-01-01 with live price data. The bar passes `INSERT OR IGNORE` (no conflict on a valid date string), so it silently corrupts the DB.

**Fix:** Return `None` instead of `0`, then check for `None` in `get_ohlcv()`:
```python
def _to_int(val: Any) -> int | None:
    ...
    except (TypeError, ValueError):
        return None
```
And filter in `get_ohlcv()`:
```python
for i in range(len(times))
if _to_int(times[i]) is not None
```

---

### H4 — IndexError Risk: VCI OHLCV Arrays May Have Mismatched Lengths
**File:** `backend/data/vci_client.py:169-173`
**Severity:** High (unhandled exception crashes symbol update)

```python
return [
    {"time": _to_int(times[i]), "open": opens[i], "high": highs[i],
     "low": lows[i], "close": closes[i], "volume": volumes[i]}
    for i in range(len(times))   # assumes all arrays same length
]
```

The VCI API is a third-party that could return malformed data (e.g., `len(opens) < len(times)` due to a partial response or API bug). This raises `IndexError` which propagates up through `update_prices_stream`, gets caught by the outer `except Exception`, and marks the symbol as `status="error"` — but the traceback is logged at WARNING not ERROR, making diagnosis harder.

**Fix:** Use `zip()` with all arrays, or assert/validate lengths before the comprehension:
```python
min_len = min(len(times), len(opens), len(highs), len(lows), len(closes), len(volumes))
return [
    {"time": _to_int(times[i]), "open": opens[i], ...}
    for i in range(min_len)
]
```

---

### H5 — `connect()` Readonly Fallback Silently Opens Writable Connection
**File:** `backend/database/connection.py:17-21`
**Severity:** High (security / data integrity)

```python
try:
    uri = f"file:{quote(str(db_path))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
except sqlite3.OperationalError:
    conn = sqlite3.connect(str(db_path))  # silently falls back to RW
```

The `OperationalError` on `mode=ro` typically means the DB file does not exist. Silently falling back to a writable connection in a function named `connect()` (with `readonly=True` default) violates caller expectations. Any read-only call path (data status, health, search) that hits this fallback can now accidentally write.

**Fix:** Either raise the error explicitly or log a CRITICAL warning before fallback:
```python
except sqlite3.OperationalError as e:
    log.critical("Cannot open DB read-only (%s), falling back to RW: %s", db_path, e)
    conn = sqlite3.connect(str(db_path))
```
Or remove the fallback entirely and let callers know the DB path is wrong.

---

### H6 — `get_data_status()` Opens 2 Separate Connections for Related Queries
**File:** `backend/data/updater.py:382-405`
**Severity:** High (performance + consistency)

```python
with connect(db_path) as conn:
    price_latest = fetch_one(conn, ...)
    price_count  = fetch_one(conn, ...)
    ratio_latest = fetch_one(conn, ...)
    ratio_count  = fetch_one(conn, ...)
# connection closed here

missing_prices = detect_missing_prices(db_path)  # opens THIRD connection
```

`detect_missing_prices()` opens its own connection. `get_db_health()` (line 488-490) does the same — opens one connection for aggregate stats then calls `detect_missing_prices()` and `detect_missing_financials()`, each opening their own. Three separate reads that could race with a write between them. At minimum this is wasteful; on a busy system, the "missing" count can be stale relative to the stats read just above it.

---

## Medium Priority Improvements

### M1 — `INSERT OR IGNORE` in `financial_updater.py` Never Updates Stale Financial Rows
**File:** `backend/data/financial_updater.py:110`
**Severity:** Medium (data staleness / correctness)

```python
cur = conn.execute(
    """INSERT OR IGNORE INTO financial_ratios ...
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
    ...
)
```

If a row was previously inserted with partial/incorrect data (e.g., source="KBS" with missing fields), re-running the updater will silently skip it due to `INSERT OR IGNORE`. There is no `ON CONFLICT DO UPDATE` (upsert) semantics. Financial data is revised annually; the first insert wins forever.

**Fix:** Use `INSERT OR REPLACE` or `INSERT ... ON CONFLICT(symbol, period) DO UPDATE SET ...` to allow re-fetching with better data. Requires a UNIQUE constraint on `(symbol, period)` if not already present.

---

### M2 — Hardcoded Absolute Windows Path in `optimizer.py`
**File:** `backend/strategy/optimizer.py:108`
**Severity:** Medium (portability / security)

```python
candidates = [
    project_dir / "sensitivity-pe5y-results.json",
    Path("D:/AI/baocaotaichinh/output/sensitivity-pe5y-results.json"),  # HARDCODED
]
```

A developer's local absolute path is in production code. This breaks on any other machine and leaks the developer's local directory structure. The function silently swallows file errors (`except Exception: continue`), so this never surfaces as an error — the endpoint just returns `{"status": "no_data"}`.

**Fix:** Remove the hardcoded path. If a secondary lookup location is needed, make it configurable via `AppConfig` or an env var.

---

### M3 — `_kbs_to_vci_row()` Silently Discards `roa` Field
**File:** `backend/data/financial_updater.py:176-191`
**Severity:** Medium (silent data loss)

```python
def _kbs_to_vci_row(kbs_row: Any) -> VCIFinancialRow:
    return VCIFinancialRow(
        symbol=kbs_row.symbol,
        year=kbs_row.year,
        quarter=None,
        eps=kbs_row.eps, pe=kbs_row.pe, pb=kbs_row.pb,
        roe=kbs_row.roe,
        revenue=kbs_row.revenue, net_profit=kbs_row.net_profit,
        bvps=kbs_row.bvps,
        issue_share=None,   # KBS doesn't have this — OK
        ev=None,            # KBS doesn't have this — OK
    )
```

`KBSFinancialRow` has a `roa` field (line 47 in `kbs_client.py`) but `_kbs_to_vci_row()` does not map it. `VCIFinancialRow` does not have an `roa` field either, so `roa` from KBS is dropped entirely during the fallback path. This is not a crash but is silent information loss.

---

### M4 — `_extract_year_from_head()` Can Raise `ValueError`
**File:** `backend/data/kbs_client.py:156-161`
**Severity:** Medium (unhandled exception)

```python
def _extract_year_from_head(data: dict) -> int:
    heads = data.get("Head", [])
    if heads:
        return int(heads[0].get("YearPeriod", 0))  # raises if value is non-numeric
```

`heads[0].get("YearPeriod", 0)` returns `0` if the key is missing, but if the API returns `"YearPeriod": "N/A"` or `null`, `int("N/A")` raises `ValueError`. This propagates up through `get_financial_summary()` → `update_financials_stream()` where it is caught by the outer `except Exception` and marks the symbol as an error — but the root cause is invisible in the logged message.

**Fix:**
```python
try:
    return int(heads[0].get("YearPeriod", 0) or 0)
except (TypeError, ValueError):
    pass
```

---

### M5 — DRY Violation: `_dec()` Defined Identically in Both Clients
**File:** `backend/data/vci_client.py:195-201`, `backend/data/kbs_client.py:147-153`
**Severity:** Medium (maintainability)

Identical 7-line function duplicated in two files. If the Decimal parsing behavior needs changing (e.g., to handle `"Infinity"` or locale-specific strings), it must be updated in two places.

**Fix:** Move to `backend/data/_common.py` or `backend/database/connection.py` and import from both clients.

---

### M6 — `asynccontextmanager` Imported But Never Used in `scheduler/__init__.py`
**File:** `backend/scheduler/__init__.py:5`
**Severity:** Medium (dead code)

```python
from contextlib import asynccontextmanager  # never referenced
```

Likely a leftover from an earlier async refactor. Harmless but misleading.

---

### M7 — `VCIClient.get_financial_ratios()` Deep-Copies a 200-Field GraphQL Payload on Every Call
**File:** `backend/data/vci_client.py:111`
**Severity:** Medium (performance)

```python
payload = json.loads(json.dumps(_RATIO_PAYLOAD_TEMPLATE))
```

`_RATIO_PAYLOAD_TEMPLATE` contains a ~4 KB GraphQL query string with 200+ fields. `json.dumps` + `json.loads` on every call (once per symbol during financial update, potentially 700+ calls) is wasteful. The only values mutated are `variables.ticker` and `variables.period`.

**Fix:** Use `copy.deepcopy()` (faster for nested dicts) or — better — restructure to only copy the mutable `variables` sub-dict:
```python
import copy
payload = copy.deepcopy(_RATIO_PAYLOAD_TEMPLATE)
# or: only mutate variables
payload = {**_RATIO_PAYLOAD_TEMPLATE, "variables": {"ticker": symbol.upper(), "period": period}}
```

---

### M8 — SSE Error Path Sends `{type:"error"}` But Not `{type:"done"}` — Client May Hang
**File:** `backend/api/data_routes.py:120-122`
**Severity:** Medium (API contract)

```python
except Exception as e:
    log.error("Price stream failed: %s", e)
    yield _sse({"type": "error", "message": "Internal update error"})
    # no "done" event follows
```

If a frontend client is waiting for `{type:"done"}` to close the EventSource, it will hang after receiving `{type:"error"}`. The error message also strips the real error detail (good for security — see positive notes), but makes server-side debugging harder. The SSE spec does not auto-close on error events.

**Fix:** Yield a `{type:"done"}` after the error event, or yield a combined `{type:"done", error: true}` payload.

---

### M9 — `count_back` Parameter Has No Upper Bound Validation
**File:** `backend/api/data_routes.py:63, 88`
**Severity:** Medium (DoS / performance)

```python
class UpdateRequest(BaseModel):
    count_back: int = 30   # no max

def stream_price_update(count_back: int = 30):  # no Query(le=...) constraint
```

A caller can pass `count_back=100000`, causing the VCI API to be asked for 100,000 bars per symbol, multiplied by hundreds of symbols. The VCI API itself may cap responses, but the intent is unclear and the server has no protection.

**Fix:**
```python
from fastapi import Query
def stream_price_update(count_back: int = Query(default=30, ge=1, le=365)):
```

---

### M10 — `yearly_performance` Endpoint Returns a Stub Without Signaling It
**File:** `backend/api/strategy_routes.py:114-118`
**Severity:** Medium (API contract / correctness)

```python
@router.get("/history/yearly")
def yearly_performance(pct: float = 14.0):
    return {"select_pct": pct, "status": "stub", "years": []}
```

Returns HTTP 200 with `"status": "stub"`. Any client treating 200 as success will silently process an empty response. Should return HTTP 501 Not Implemented or remove the route until implemented.

---

## Low Priority Suggestions

### L1 — `get_config()` Creates a New `AppConfig` on Every Call
**File:** `backend/config.py:72-73`
**Severity:** Low (performance / correctness)

```python
def get_config() -> AppConfig:
    return AppConfig()
```

`AppConfig` is a frozen dataclass — identical instances are created 4 times at module load (once per route module + `main.py`). No behavioral bug since the fields are deterministic from env vars, but any env var change between calls would produce different instances silently. A simple module-level singleton (`_cfg = AppConfig()`) would be cleaner.

---

### L2 — `db_path` Exposed in Public `/api/health` Endpoint
**File:** `backend/main.py:56`
**Severity:** Low (information disclosure)

```python
return {
    "status": "ok",
    "db_path": str(_cfg.db_path),   # leaks local filesystem path
    "db_exists": _cfg.db_path.exists(),
}
```

Exposes the server's filesystem layout. Should return only `"status"` and `"db_exists"` (or a boolean like `"db_ready"`).

---

### L3 — Deferred Import of `datetime` Inside Function Body
**File:** `backend/api/strategy_routes.py:42`, `backend/strategy/optimizer.py:44`
**Severity:** Low (style)

```python
def get_portfolio(...):
    import datetime    # deferred import
```

`datetime` is a stdlib module with no import cost. Deferred imports here serve no purpose (no circular import risk) and obscure the module's dependencies. Move to the top-level.

---

### L4 — Deferred Import of `_load_sensitivity_data` (Private Function) in Route Handler
**File:** `backend/api/strategy_routes.py:98`
**Severity:** Low (style / encapsulation)

```python
from ..strategy.optimizer import _load_sensitivity_data  # private, underscore-prefixed
```

Importing a private function across module boundaries from inside a handler body is a code smell. The sensitivity data loading should be exposed through a public API in `optimizer.py` or inlined into `optimize()`.

---

### L5 — `KBSClient.get_ohlcv()` Uses `count_back` Parameter Name But KBS API Uses `count`
**File:** `backend/data/kbs_client.py:116`
**Severity:** Low (clarity)

```python
def get_ohlcv(self, symbol: str, count_back: int = 30) -> ...:
    params = {"symbol": sym, "timeFrame": "D", "count": count_back}
```

Externally the param is `count_back` (matching VCI convention) but the KBS API field is `count`. No bug, but the inconsistency in parameter naming (`count_back` vs `count`) is worth aligning in a comment or via a named alias.

---

### L6 — `_RATIO_PAYLOAD_TEMPLATE` Built From Multi-Line String Concatenation
**File:** `backend/data/vci_client.py:25-68`
**Severity:** Low (maintainability)

The GraphQL payload is assembled from a giant multi-line string literal using implicit string concatenation inside `json.loads()`. This is unreadable. The actual query text should be a `.graphql` file or a multi-line triple-quoted string assigned to a constant, not embedded in `json.loads("..." "..." "...")`.

---

### L7 — `KBSClient._extract_year_from_head()` Has Deferred `import datetime`
**File:** `backend/data/kbs_client.py:160`
**Severity:** Low (style)

```python
def _extract_year_from_head(data: dict) -> int:
    heads = data.get("Head", [])
    if heads:
        return int(heads[0].get("YearPeriod", 0))
    import datetime          # deferred import of stdlib
    return datetime.date.today().year
```

`datetime` is already importable at module level. Move import to top of file.

---

## Positive Observations

- **Parameterized queries throughout** — zero SQL injection risk. Every `conn.execute()` call across all files uses `?` placeholders with a separate `params` tuple.
- **`connect_rw()` rollback on exception** — the `except Exception: conn.rollback(); raise` pattern in `connection.py:40-43` is correct and prevents partial writes.
- **`_ts_to_date()` is robust** — handles int, float, numeric string, and proper date strings; returns `None` for garbage. Good defensive design.
- **KBS fallback logic is clear** — the two-phase VCI-then-KBS logic in `update_prices_stream()` is readable and the skip-reason messaging is detailed.
- **`detect_missing_prices()` uses actual trading days** — the HAVING COUNT(DISTINCT symbol) >= 50 heuristic avoids counting weekends/holidays as missing days. Smart.
- **Streaming progress design** — `SymbolProgress` and `FinancialProgress` dataclasses give the SSE clients rich per-symbol telemetry without coupling the update logic to HTTP.
- **`VCIClient.__enter__`/`__exit__`** — proper context manager implementation ensuring `httpx.Client` is always closed.
- **`fix_integer_timestamps()` migration is idempotent** — the two-step DELETE-then-UPDATE approach (delete conflicts first, then convert) correctly handles re-runs.

---

## Recommended Actions (Priority Order)

1. **[Critical — C1]** Fix route ordering in `verify_routes.py`: move `/batch/check` before `/{symbol}`.
2. **[High — H3]** Fix `_to_int()` to return `None` on failure and filter in `get_ohlcv()` to prevent 1970-01-01 rows.
3. **[High — H2]** Enable `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000` in `connect_rw()` to prevent "database is locked" errors under concurrent scheduler + SSE writes.
4. **[High — H4]** Replace `range(len(times))` with `zip(times, opens, highs, lows, closes, volumes)` in `VCIClient.get_ohlcv()` to guard against mismatched array lengths.
5. **[High — H1]** Refactor `update_prices()` to use a single `connect_rw` connection outside the symbol loop.
6. **[High — H5]** Log CRITICAL (or raise) when `connect()` falls back from read-only to writable mode.
7. **[Medium — M2]** Remove hardcoded `"D:/AI/baocaotaichinh/..."` path from `optimizer.py:108`.
8. **[Medium — M1]** Change `INSERT OR IGNORE` to `INSERT OR REPLACE` (or proper upsert) in `financial_updater.py` so revised financial data can be re-fetched.
9. **[Medium — M8]** Add `{type:"done"}` yield after `{type:"error"}` in both SSE generators.
10. **[Medium — M9]** Add `Query(ge=1, le=365)` constraint on `count_back` in `stream_price_update()`.
11. **[Medium — M5]** Consolidate `_dec()` into a shared module.
12. **[Medium — M6]** Remove unused `asynccontextmanager` import from `scheduler/__init__.py`.
13. **[Medium — M4]** Wrap `int(heads[0].get("YearPeriod", ...))` in try/except in `_extract_year_from_head()`.
14. **[Low — L2]** Remove `"db_path"` from the public `/api/health` response.
15. **[Low — M10]** Return HTTP 501 or remove `GET /history/yearly` stub endpoint.

---

## Metrics

- **Type coverage:** Moderate — all public functions have return type hints; some private helpers lack them (`_run_price_update`, `_sse`). No `mypy` config found.
- **Linting issues:** 2 unused imports (scheduler `asynccontextmanager`; `fix_integer_timestamps` imported in `data_routes.py` but not exposed as an endpoint — it is used as an import for the lifespan call in main).
- **Critical issues:** 1
- **High issues:** 5
- **Medium issues:** 10
- **Low issues:** 7

---

## Unresolved Questions

1. Is there a UNIQUE constraint on `(symbol, period)` in the `financial_ratios` table? If not, `INSERT OR IGNORE` and `INSERT OR REPLACE` both have undefined behavior on duplicates. The schema was not included in the review scope.
2. The `fix_integer_timestamps()` function is imported in `data_routes.py` (line 18) but is not wired to any API endpoint — only called at startup in `lifespan()`. Was an admin endpoint for manual re-runs intended?
3. `KBSConfig.base_url` and `KBSConfig.price_url` are defined in config but the `KBSClient` uses module-level `_PROFILE_URL`, `_FINANCE_URL`, `_OHLC_URL` constants instead of reading from config. The config URLs are never used — intentional or oversight?
