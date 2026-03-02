# Phase 2 — Backend Critical + High Fixes

**Priority:** P1
**Scope:** backend/ Python files (3,862 LOC)

---

## CRIT-BE1: `GET /api/verify/batch/check` Permanently Unreachable

**File:** `backend/api/verify_routes.py:15 vs 45`

`@router.get("/{symbol}")` registered first catches ALL single-segment paths including `/batch`. The batch endpoint at line 45 is never matched.

**Fix:** Swap registration order — literal routes before parameterized ones.
```python
# Move batch/check ABOVE /{symbol}
@router.get("/batch/check")  # literal first
def batch_check(...): ...

@router.get("/{symbol}")     # param catch-all second
def verify_symbol(...): ...
```

---

## HIGH-BE1: N+1 DB Connections in `_insert_bars()`

**File:** `backend/data/updater.py:193`

`_insert_bars()` opens a new `connect_rw()` per symbol call. 700+ symbols = 700+ open/commit/close cycles.

**Fix:** Accept an optional connection parameter or batch inserts.

---

## HIGH-BE2: No WAL Mode → Concurrent Write Locking

**File:** `backend/database/connection.py`

No `PRAGMA journal_mode=WAL` or `PRAGMA busy_timeout` anywhere. Scheduler thread + SSE stream writing simultaneously → `database is locked` errors silently swallowed.

**Fix:** Add to `connect_rw()`:
```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=5000")
```

---

## HIGH-BE3: `_to_int()` Returns 0 → 1970-01-01 Bars Inserted

**File:** `backend/data/vci_client.py:192`

Unparseable VCI timestamp → `_to_int()` returns `0` → `_ts_to_date(0)` → `"1970-01-01"` → inserted as valid row. Silent data corruption.

**Fix:** Return `None` from `_to_int()` on failure, filter None before building bar dict.

---

## HIGH-BE4: IndexError on Mismatched VCI Array Lengths

**File:** `backend/data/vci_client.py:169-173`

`for i in range(len(times))` then `opens[i]`, `highs[i]` without length check.

**Fix:** Use `zip(times, opens, highs, lows, closes, volumes)`.

---

## HIGH-BE5: Readonly Connect Silently Falls Back to Writable

**File:** `backend/database/connection.py:20-21`

`except sqlite3.OperationalError` catches DB-not-found and opens writable connection silently.

**Fix:** Only catch URI mode unsupported, not all OperationalError.

---

## Medium Issues Summary

| ID | Issue | File |
|----|-------|------|
| M1 | `INSERT OR IGNORE` in financial_updater — can never update existing rows | financial_updater.py:110 |
| M2 | Hardcoded Windows path `D:/AI/baocaotaichinh/...` | optimizer.py:108 |
| M3 | `_kbs_to_vci_row()` drops `roa` field silently | financial_updater.py:176 |
| M4 | `int(heads[0].get("YearPeriod", 0))` → ValueError on "N/A" | kbs_client.py:159 |
| M5 | `_dec()` duplicated in vci_client.py and kbs_client.py | DRY violation |
| M6 | Unused asynccontextmanager import | scheduler/__init__.py:5 |
| M7 | Deep copy via json.dumps/loads per symbol for GraphQL payload | vci_client.py:111 |
| M8 | SSE error yields no "done" event → client hangs | data_routes.py:120 |
| M9 | count_back param has no upper bound | data_routes.py:63,88 |
| M10 | Stub endpoint returns 200 instead of 501 | strategy_routes.py:115 |
