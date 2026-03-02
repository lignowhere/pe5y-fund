# Bug Investigation: "Behind Market" Counter Not Resolved After Update Prices

**Date:** 2026-03-02
**DB:** `D:/AI/baocaotaichinh/vietnam_stocks.db`
**Affected files:** `backend/data/updater.py`, `backend/data/vci_client.py`

---

## Executive Summary

"Behind market" counter shows 369-387 symbols (user saw 455 at time of report; DB state changes with each update run). After clicking "Update Prices", all symbols report `"VCI: 30 bars but all already existed"`. Four root causes identified, one primary bug dominates.

**Primary cause:** KBS fallback only activates when VCI returns **zero** bars. VCI returns 30 stale bars (already in DB) for suspended/inactive symbols — KBS is never tried. The counter never clears.

---

## Root Causes

### BUG 1 (PRIMARY) — KBS fallback condition is too narrow

**File:** `backend/data/updater.py` **line 213**

```python
# CURRENT (broken):
bars = vci.get_ohlcv(sym, count_back=count_back)
if not bars and kbs is not None:          # line 213 — only triggers on EMPTY list
    try:
        bars = kbs.get_ohlcv(sym, count_back=count_back)
        source = "KBS"
    except Exception:
        bars = []
```

**What happens:**
1. VCI `get_ohlcv("AMD", countBack=30)` returns 30 bars — the last 30 AVAILABLE trading days for that ticker, going back from now.
2. For a symbol suspended since 2025-12-22, those 30 bars are Dec 2025 data.
3. All 30 are already in DB → `INSERT OR IGNORE` inserts 0 rows → `sym_bars = 0`.
4. `bars` is a truthy non-empty list → `if not bars` is **False** → KBS never tried.
5. `skip_reason = "VCI: 30 bars but all already existed"` → status `"skip"`.
6. Symbol remains stale. Next detect cycle flags it again.

**Affected symbols:** 167 symbols whose last trade was Dec 2025 (Tet holiday; many are UPCOM low-liquidity names). Also affects any symbol that VCI "fills in" with older data rather than returning zero bars.

**Fix:**
```python
bars = vci.get_ohlcv(sym, count_back=count_back)

# NEW: also try KBS if VCI bars are stale (latest bar < gap_threshold)
vci_is_stale = False
if bars:
    latest_vci_date = max(
        _ts_to_date(b["time"]) for b in bars if b.get("time")
    )
    if latest_vci_date is not None and latest_vci_date < gap_threshold:
        vci_is_stale = True

if (not bars or vci_is_stale) and kbs is not None:
    try:
        kbs_bars = kbs.get_ohlcv(sym, count_back=count_back)
        source = "KBS"
        if kbs_bars:
            bars = kbs_bars   # replace stale VCI bars with KBS bars
    except Exception:
        pass  # keep VCI bars if KBS fails
```

`gap_threshold` needs to be passed into `update_prices_stream()` as a parameter (or recomputed from the DB).

---

### BUG 2 (SECONDARY) — No date-range awareness in stream updater

**File:** `backend/data/updater.py` **lines 247-253**

```python
if sym_bars > 0:
    updated += 1
    status = "ok"
else:
    status = "skip"
    skip_reason = f"{source}: {len(bars)} bars but all already existed"
```

After inserting bars, the code does not check whether the _freshest_ inserted bar actually covers the gap. A symbol that had bars inserted but whose MAX(time) is still below `gap_threshold` will be immediately re-flagged on the next `detect_missing_prices` call.

This is a reporting/logic gap, not a data corruption. It means `update_prices_stream` reports `status="ok"` but the symbol is still "behind" according to `detect_missing_prices`.

**Fix:** After inserting, verify the symbol's new MAX(time) vs gap_threshold. If still behind, yield a warn-level skip with explanation rather than "ok".

---

### BUG 3 — 1,380 rows stored as INTEGER unix timestamps (not TEXT dates)

**DB evidence:**
```
typeof(time): integer → 1,380 rows, 46 symbols
typeof(time): text    → 4,822,317 rows
```

**Cause:** An older code path ran `INSERT` without applying `_ts_to_date()`. The `time` column has `DATE` affinity but SQLite does not enforce type; it stored the raw integer.

**Impact on UNIQUE constraint:** SQLite `UNIQUE(symbol, time)` treats `integer 1770681600` and `text "2026-02-10"` as **different values**. Result: 10 confirmed duplicate rows for TVH (same calendar date stored twice, once as int and once as text).

**Impact on `detect_missing_prices`:** `MAX(time)` with mixed types picks the TEXT value (SQLite's type comparison rules: TEXT > INTEGER). So the integer rows don't directly cause false positives in `detect_missing_prices` — the text MAX is used correctly. However, duplicate data wastes storage and could skew OHLCV queries.

**Fix (DB migration):**
```sql
-- Step 1: identify integer-timestamp rows that don't have a text duplicate
-- Step 2: UPDATE time = date(time, 'unixepoch') for those rows
-- Step 3: delete any remaining integer-timestamp duplicates

-- Safe migration script:
BEGIN;
-- Convert integer timestamps to date strings (no duplicate)
UPDATE stock_price_history
SET time = date(time, 'unixepoch')
WHERE typeof(time) = 'integer'
  AND NOT EXISTS (
      SELECT 1 FROM stock_price_history t2
      WHERE t2.symbol = stock_price_history.symbol
        AND t2.time = date(stock_price_history.time, 'unixepoch')
        AND typeof(t2.time) = 'text'
  );
-- Delete integer rows that now have text duplicates (leftover after INSERT OR IGNORE)
DELETE FROM stock_price_history
WHERE typeof(time) = 'integer';
COMMIT;
```

---

### BUG 4 (DESIGN) — Inactive/suspended symbols permanently inflate counter

**DB evidence:**
- 167 symbols with last trade `2025-12-22` (last trading day before Tet New Year 2026).
- These are valid entries in `stock_exchange` with exchange `UPCOM`/`HNX`/`HSX`.
- Many are genuinely suspended, halted, or extremely illiquid.
- `detect_missing_prices` flags all of them as "behind" forever.

This is not technically a code bug — the query is correct — but a **design gap**. The counter includes symbols that cannot be updated regardless of retries.

**Recommended fix:** Add a `max_gap_trading_days` threshold. If a symbol has been stale for more than, say, 60 trading days (i.e., `gap_threshold - MAX(time) > 60 market days`), exclude it from `detect_missing_prices` as presumed inactive. Optionally, surface these in a separate "possibly delisted" list.

Alternatively, add an `active` flag to the `stocks` table and exclude inactive tickers.

---

### BUG 5 (MINOR) — `_ts_to_date` uses local timezone

**File:** `backend/data/updater.py` **line 37**

```python
return datetime.datetime.fromtimestamp(numeric).strftime("%Y-%m-%d")
```

`fromtimestamp` uses the server's local timezone. VCI timestamps are midnight UTC (confirmed: `ts=1718323200` → `UTC 2024-06-14 00:00:00` → local `2024-06-14 07:00:00` in UTC+7). On a server in UTC+7 this is fine. On a server in UTC-X (e.g. UTC-5) the date would shift to the previous day, causing mismatches with text rows and the UNIQUE constraint.

**Fix:**
```python
return datetime.datetime.fromtimestamp(numeric, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
```

---

## Evidence Summary

| Metric | Value |
|--------|-------|
| Symbols flagged by `detect_missing_prices` | 369 (DB state 2026-03-02) |
| `gap_threshold` (market_days[3]) | `2026-02-25` |
| Latest market day | `2026-03-02` |
| Symbols with integer timestamp rows | 46 |
| Integer timestamp rows total | 1,380 |
| Confirmed int+text duplicate rows | 10 (TVH) |
| Symbols last traded 2025-12-22 | 167 |
| Symbols last traded in 2026 but < gap_threshold | 197 |

---

## Fix Priority

1. **BUG 1** — Fix immediately. Single-line logic change in `update_prices_stream`, requires passing `gap_threshold` into the function.
2. **BUG 3** — Run migration SQL once. Low risk, no code change needed.
3. **BUG 4** — Design decision: add `active` flag or staleness exclusion to `detect_missing_prices`. Eliminates ~167 phantom entries from the counter.
4. **BUG 2** — Minor reporting fix. Change `status="ok"` to `status="partial"` when bars inserted but still below gap_threshold.
5. **BUG 5** — One-line fix, low urgency if server is in UTC+X timezone.

---

## Suggested `update_prices_stream` Fix (Minimal)

In `backend/data/updater.py`, change lines 211-218:

```python
# BEFORE:
bars = vci.get_ohlcv(sym, count_back=count_back)
if not bars and kbs is not None:
    try:
        bars = kbs.get_ohlcv(sym, count_back=count_back)
        source = "KBS"
    except Exception:
        bars = []

# AFTER: add gap_threshold parameter to the function, then:
bars = vci.get_ohlcv(sym, count_back=count_back)
vci_latest = None
if bars:
    dates = [_ts_to_date(b["time"]) for b in bars if b.get("time")]
    dates = [d for d in dates if d]
    vci_latest = max(dates) if dates else None

should_try_kbs = (not bars) or (
    kbs is not None and gap_threshold is not None
    and vci_latest is not None and vci_latest < gap_threshold
)
if should_try_kbs and kbs is not None:
    try:
        kbs_bars = kbs.get_ohlcv(sym, count_back=count_back)
        if kbs_bars:
            bars = kbs_bars
            source = "KBS"
    except Exception:
        pass  # fall through with VCI bars
```

Also update function signature:
```python
def update_prices_stream(
    db_path: Path,
    symbols: list[str],
    vci: VCIClient,
    kbs: Optional[KBSClient] = None,
    count_back: int = 30,
    gap_threshold: Optional[str] = None,   # NEW
) -> Iterator[SymbolProgress]:
```

In `data_routes.py` line 103, pass `gap_threshold` from the detect step:
```python
# compute gap_threshold before starting stream
market_days = _get_market_days(_cfg.db_path)
gt = market_days[3]["time"] if len(market_days) > 3 else None

for prog in update_prices_stream(
    _cfg.db_path, symbols, vci, kbs, count_back, gap_threshold=gt
):
```

---

## Unresolved Questions

1. Does KBS return MORE RECENT bars than VCI for suspended UPCOM stocks? If KBS also returns stale data, BUG 1 fix won't help those symbols — they'd still be permanently "behind". Need to verify KBS OHLC endpoint behavior for inactive tickers.
2. What caused the 1,380 integer-timestamp rows? Was there an older code path before `_ts_to_date` was introduced, or a direct DB import? Understanding this prevents recurrence.
3. Should genuinely inactive/suspended symbols be removed from `stock_exchange` or flagged with a status column? The current design has no lifecycle management for delistings.
