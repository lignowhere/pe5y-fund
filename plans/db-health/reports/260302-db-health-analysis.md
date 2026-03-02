# DB Health Analysis — pe5y-fund / vietnam_stocks.db
Date: 2026-03-02
DB path: `D:\AI\baocaotaichinh\vietnam_stocks.db` (via `PE5Y_DB_PATH` in `.env`)
SQLite version: 3.50.4

---

## Executive Summary

| Category | Status | Severity |
|---|---|---|
| Integer timestamps remaining | CLEAN — 0 integer rows | OK |
| Duplicate rows (statement tables) | 321,275 excess rows across 3 tables | CRITICAL |
| Unique constraint bypass (NULL quarter) | Root cause of all statement duplicates | CRITICAL |
| sqlite_sequence corruption | 6 tables with duplicate sequence entries | HIGH |
| Orphaned price history (index symbols) | 39,445 rows, 19 symbols | MEDIUM |
| Orphaned stock_exchange rows | 1,449 tickers (bonds), 1,301 unknown exchanges | MEDIUM |
| OHLCV close=0 on actual stocks | 7,953 rows across 116 symbols | MEDIUM |
| OHLCV high < low | 7 rows | LOW |
| Missing composite index (financial_ratios) | (symbol, year, quarter) composite absent | MEDIUM |
| Redundant indexes | 3 confirmed redundant indexes | LOW |
| Fragmentation | 0.0% — VACUUM not needed | OK |
| Integrity check | PASSED | OK |

---

## Section 1: Schema Summary

22 tables total. Key tables:

| Table | PK | UNIQUE Constraint | FK to stocks |
|---|---|---|---|
| `stocks` | `ticker` | — | — |
| `stock_price_history` | `id` (AUTOINCREMENT) | `(symbol, time)` | YES |
| `stock_exchange` | `(ticker, exchange)` | — | YES |
| `financial_ratios` | `id` (AUTOINCREMENT) | `(symbol, period, year, quarter)` | YES |
| `balance_sheet` | `id` (AUTOINCREMENT) | `(symbol, period, year, quarter)` | YES |
| `income_statement` | `id` (AUTOINCREMENT) | `(symbol, period, year, quarter)` | YES |
| `cash_flow_statement` | `id` (AUTOINCREMENT) | `(symbol, period, year, quarter)` | YES |
| `company_overview` | `symbol` | — | YES |
| `stock_intraday` | `(symbol, time)` | — | YES |
| `events`, `news`, `shareholders`, `officers` | varies | — | YES |

No FK enforcement (SQLite FK pragma not enabled via WAL, no `PRAGMA foreign_keys=ON` in schema).

---

## Section 2: Row Counts

```
stock_price_history        4,823,384 rows  <- largest table
cash_flow_statement          180,681 rows
income_statement             180,615 rows
balance_sheet                180,419 rows
financial_ratios              74,709 rows
shareholders                  32,596 rows
stock_exchange                 3,179 rows
stocks                         1,730 rows
company_overview               1,661 rows
stock_industry                 1,584 rows
eps_backfill_raw               1,180 rows
events                           953 rows
update_log                       885 rows
stock_intraday                   708 rows
stock_index                      412 rows
news                             300 rows
industries                       155 rows
officers                          46 rows
subsidiaries                       6 rows
indices                            5 rows
exchanges                          4 rows
financial_reports                  0 rows  <- EMPTY
TOTAL                      5,485,212 rows across 22 tables
```

`financial_reports` table is entirely empty despite having 4 indexes defined.

---

## Section 3: Data Integrity Findings

### 3A. Foreign Key Orphans

| Relationship | Orphan Rows | Sample Values | Verdict |
|---|---|---|---|
| `stock_price_history.symbol -> stocks.ticker` | 39,445 | HNX30, HNXINDEX, VN30, VNINDEX, VN100 + 14 more | Expected — index/market data stored with stock prices |
| `stock_exchange.ticker -> stocks.ticker` | 1,449 rows | 41B5G3000, 41BAG3000 … (bond codes) | MEDIUM — bond instruments in exchange table but not in stocks |
| `stock_exchange.exchange -> exchanges.exchange` | 1,301 rows | 'BOND' (82), 'DELISTED' (1,219) | MEDIUM — unknown exchange values not in `exchanges` master table |
| All other FK relationships | 0 | — | OK |

**Detail on price history orphans:** all 39,445 rows belong to exactly 19 market-index symbols (VNINDEX, VN30, HNX30, etc.). These are intentionally stored here for index tracking but the `stocks` table only holds individual company stocks. Not a bug — but FK enforcement would reject these rows.

**Detail on stock_exchange orphans:** 1,449 tickers with format `41B5G3000` are bond instrument codes from an exchange data feed. The `exchanges` table has only 4 entries (`HSX`, `HNX`, `UPCOM`, and one other) but the feed includes `BOND` and `DELISTED` as exchange values.

### 3B. Duplicate Rows — CRITICAL

The UNIQUE constraint `(symbol, period, year, quarter)` on financial statement tables **does not prevent duplicates when `quarter IS NULL`** because SQLite treats `NULL != NULL` in unique indexes (ISO SQL behavior). This allows unlimited re-insertions of annual (quarter=NULL) records.

```
balance_sheet:        180,419 total,  73,485 distinct keys,  106,934 excess rows (59.3% duplication)
income_statement:     180,615 total,  73,493 distinct keys,  107,122 excess rows (59.3% duplication)
cash_flow_statement:  180,681 total,  73,462 distinct keys,  107,219 excess rows (59.3% duplication)
financial_ratios:      74,709 total,  74,229 distinct keys,      480 excess rows  (0.6% duplication)
```

**Root cause:** Annual re-fetch jobs (VCI source) use `INSERT OR IGNORE` or plain `INSERT`, but since `quarter IS NULL` bypasses the UNIQUE constraint, every run inserts new duplicate rows. Evidence from `MKV` balance_sheet 2013: 13 rows across 5 distinct `created_at` dates (Dec 2025, Jan 2026 x2, Feb 15 x2).

**Are duplicate rows data-different?**
- `balance_sheet`: 13,871 groups have DIFFERENT `data_json` among duplicates (not safe blind dedup), 190 groups are identical
- `financial_ratios` BBC: all 5 copies per year are **identical** (same eps, roe, mcap values) — safe to dedup

**Rows recoverable by keeping MAX(id) per group:**
```
balance_sheet:        106,934 rows removable
income_statement:     107,122 rows removable
cash_flow_statement:  107,219 rows removable
financial_ratios:         480 rows removable
TOTAL:                321,755 rows removable (~6% of DB)
```

### 3C. NULL Values in Critical Columns

All critical columns are healthy. Notable non-zero NULLs:

```
sph.close                   33 NULL  (0.0%) — minor, older data gaps
financial_ratios.roa     1,516 NULL  (2.0%) — expected for some sectors
financial_ratios.roe     1,179 NULL  (1.6%) — expected
financial_ratios.market_cap  1,011 NULL (1.4%) — expected for less-covered stocks
income_statement.revenue    18 NULL  (0.0%) — negligible
```

No showstopper NULL issues.

### 3D. Integer Timestamps — FULLY MIGRATED

```
typeof(time) = 'text':  4,823,384 rows  (100%)
typeof(time) = 'integer': 0 rows
Non-YYYY-MM-DD text values: 0
Date range: 2004-08-18 -> 2026-03-02
```

Migration from `fix_integer_timestamps()` is complete and clean.

### 3E. Mixed Data Types

No mixed types detected in any column. All REAL columns store REAL, INTEGER columns store INTEGER consistently.

### 3F. OHLCV Data Anomalies

```
close <= 0 (actual stocks):   7,953 rows across 116 symbols
volume = 0 (actual stocks):  1,595,166 rows
high < low (invalid OHLC):         7 rows
time > 2026-03-02 (future):         0 rows
```

- **close=0**: Likely suspended/halted trading days. Sample: PMC had O=H=L=C=0 with non-zero volume in Aug-Sep 2025 — possible data source artifact for stocks under trading halt.
- **volume=0**: 1.59M rows (~33% of all price history). This is normal for Vietnam market — stocks with no trading activity on given days still get OHLC records with volume=0.
- **high < low (7 rows)**: Genuine data quality defects in historical data (EPH 2018, HIG 2017, HRT 2017, PJC 2008, SPA 2017). Small enough to be historical source errors. Recommend flagging but not deleting.

---

## Section 4: Index Coverage

### Existing Indexes (41 total explicit)

All critical query patterns are covered:

| Table | Indexes | Verdict |
|---|---|---|
| `stock_price_history` | `(symbol)`, `(time)`, `(symbol,time)` composite | GOOD — all 3 present; composite is covering for most queries |
| `stocks` | `(ticker)` explicit + PK autoindex | REDUNDANT — explicit idx_stocks_ticker is redundant with PK |
| `stock_exchange` | `(ticker)`, `(exchange)` | ADEQUATE |
| `financial_ratios` | `(symbol)`, `(year)`, `(period)` separate | MISSING composite |
| `balance_sheet` | `(symbol)`, `(year)`, `(period)` separate | MISSING composite |
| `income_statement` | `(symbol)`, `(year)`, `(period)` separate | MISSING composite |
| `cash_flow_statement` | `(symbol)`, `(year)`, `(period)` separate | MISSING composite |

### Query Plan Analysis (EXPLAIN QUERY PLAN)

**Good plans:**
- `detect_missing_prices` market days query: `SCAN stock_price_history USING INDEX idx_price_history_time` — optimal
- `detect_missing_prices` per-symbol subquery: `SCAN USING COVERING INDEX idx_price_history_symbol_time` — optimal
- `detect_missing_prices` full JOIN: uses `idx_stock_exchange_exchange`, autoindex on stocks PK, temp B-tree for ORDER BY — acceptable

**Suboptimal plans:**
- `financial_ratios WHERE symbol=? AND year=? AND quarter IS NULL` — uses `idx_ratios_year (year=?)` only, then filters symbol and quarter. Should use composite `(symbol, year, quarter)`.
- `balance_sheet/income_statement/cash_flow_statement` same pattern — only year index used, no composite.
- `NOT IN (SELECT DISTINCT symbol FROM financial_ratios WHERE year=? AND quarter IS NULL)` — uses year index + `USE TEMP B-TREE FOR DISTINCT`. A composite `(year, quarter, symbol)` would eliminate the temp B-tree.

### Missing Indexes (Recommended)

```sql
-- HIGH PRIORITY: financial statement tables annual lookup
CREATE INDEX idx_ratios_symbol_year_quarter ON financial_ratios(symbol, year, quarter);
CREATE INDEX idx_balance_sheet_symbol_year_quarter ON balance_sheet(symbol, year, quarter);
CREATE INDEX idx_income_statement_symbol_year_quarter ON income_statement(symbol, year, quarter);
CREATE INDEX idx_cash_flow_symbol_year_quarter ON cash_flow_statement(symbol, year, quarter);

-- LOW PRIORITY: covering index for NOT IN subquery pattern
CREATE INDEX idx_ratios_year_quarter_symbol ON financial_ratios(year, quarter, symbol);
```

### Redundant Indexes (Can Drop)

```sql
-- Redundant: ticker is PK, SQLite auto-creates index
DROP INDEX IF EXISTS idx_stocks_ticker;

-- Redundant: company_overview.symbol IS the PRIMARY KEY
DROP INDEX IF EXISTS idx_company_overview_symbol;

-- Potentially redundant: idx_price_history_symbol_time(symbol,time) already covers symbol-only lookups
-- Keep idx_price_history_symbol for now since it's used in SCAN patterns; revisit after composite test
```

---

## Section 5: Storage Analysis

```
DB file size:       1,567.9 MB (1.53 GB)
Page size:          4,096 bytes
Total pages:        401,379
Free pages:         24 (0.0% fragmentation)
Recoverable space:  0.1 MB
Journal mode:       WAL (Write-Ahead Logging)
WAL file:           0 KB (checkpointed, clean)
SHM file:           32 KB (normal)
Auto-vacuum:        NONE
PRAGMA integrity_check: OK
```

**Storage is healthy.** No fragmentation. WAL is checkpointed. The 1.53 GB size is driven by:
- `stock_price_history` (4.8M rows with OHLCV data) — primary contributor
- Financial statement tables (540K combined rows) — 321K are duplicates consuming ~30% of those tables unnecessarily

**After deduplication**, estimated recoverable: ~321K rows x ~2KB avg row size = ~640 MB potential reduction across statement tables.

---

## Section 6: sqlite_sequence Anomaly

6 tables have **duplicate entries** in `sqlite_sequence`:

```
stock_price_history:  seqs=[6,103,276 and 5,901,754]  <- different values!
balance_sheet:        seqs=[276,454, 276,454]  <- identical
cash_flow_statement:  seqs=[274,558, 274,558]  <- identical
income_statement:     seqs=[274,545, 274,545]  <- identical
financial_ratios:     seqs=[97,329, 95,973]    <- different values!
update_log:           seqs=[885, 885]          <- identical
```

This is a **database corruption indicator**. SQLite's `sqlite_sequence` table should have exactly one row per AUTOINCREMENT table. Duplicate rows suggest the DB was modified by an external tool (e.g., bulk import via attach/detach, or a botched migration script) that created a second sequence entry rather than updating the existing one.

The `stock_price_history` and `financial_ratios` cases with **different** sequence values are most concerning — SQLite will use the first matching row found, which may be the lower value, potentially allowing ID collisions on future inserts if the true max ID exceeds the tracked sequence.

**Immediate check needed:** confirm max actual IDs vs tracked sequences.

---

## Section 7: Critical Findings Summary

### CRITICAL-1: Duplicate Financial Statement Rows (321,755 excess rows)

**Problem:** `UNIQUE(symbol, period, year, quarter)` constraint allows unlimited duplicate annual records (quarter=NULL) because SQLite's NULL uniqueness behavior means NULL != NULL in unique checks. Every re-fetch of annual data inserts a new row.

**Evidence:** MKV balance_sheet 2013 has 13 rows (5 batch runs). BBC financial_ratios 2022 has 5 identical rows from 4 runs on 2026-03-02 alone (08:04, 08:05, 08:15, 08:15).

**Fix options:**
1. **Schema fix (preferred):** Replace `UNIQUE(symbol, period, year, quarter)` with a partial unique index:
   ```sql
   -- For annual records (quarter IS NULL): unique on (symbol, period, year)
   CREATE UNIQUE INDEX uq_balance_sheet_annual ON balance_sheet(symbol, period, year) WHERE quarter IS NULL;
   -- For quarterly: keep original constraint behavior
   CREATE UNIQUE INDEX uq_balance_sheet_quarterly ON balance_sheet(symbol, period, year, quarter) WHERE quarter IS NOT NULL;
   ```
2. **Upsert fix (interim):** Change INSERT to `INSERT OR REPLACE` or use `INSERT ... ON CONFLICT(symbol, period, year) WHERE quarter IS NULL DO UPDATE SET ...` (SQLite 3.24+)
3. **Deduplication cleanup:** Run dedup keeping MAX(id) per (symbol, period, year) group for annual records

**NOTE:** 13,871 balance_sheet dup groups have DIFFERENT `data_json` — the data was re-fetched and may have been updated. Keeping MAX(id) (most recent) is the correct strategy. But the different data_json means these aren't true duplicates — the source data changed between fetches. This is a data versioning issue, not just a dedup issue.

### CRITICAL-2: sqlite_sequence Corruption

**Problem:** 6 tables have multiple `sqlite_sequence` entries. For `stock_price_history` (seqs: 6,103,276 vs 5,901,754) and `financial_ratios` (seqs: 97,329 vs 95,973), the values differ.

**Risk:** If SQLite reads the lower sequence value and max actual ID in the table exceeds it, the next INSERT could attempt an ID that already exists, causing an `UNIQUE constraint failed: table.id` error or silently colliding.

**Fix:**
```sql
-- Check actual max IDs
SELECT MAX(id) FROM stock_price_history;  -- compare to 6,103,276
SELECT MAX(id) FROM financial_ratios;    -- compare to 97,329

-- Fix: delete duplicate entries, keep correct (max) sequence value
DELETE FROM sqlite_sequence WHERE name='stock_price_history' AND seq < (
    SELECT MAX(seq) FROM sqlite_sequence WHERE name='stock_price_history'
);
-- Repeat for all affected tables
```

### HIGH: Missing Composite Indexes

Financial statement queries filter on `(symbol, year, quarter)` but only single-column year indexes exist. The optimizer uses the year index then post-filters — adequate now but will degrade as tables grow further.

---

## Recommended Actions (Prioritized)

| Priority | Action | Est. Impact |
|---|---|---|
| P0 | Fix `sqlite_sequence` duplicates — run dedup DELETE + verify max IDs match | Prevents potential ID collision crashes |
| P0 | Fix duplicate insertion logic — add partial unique index or upsert ON CONFLICT for quarter IS NULL | Stops ongoing data bloat |
| P1 | Dedup financial statement tables (keep MAX(id) per annual group) | Recovers ~640 MB, speeds up all queries on those tables |
| P2 | Add composite indexes `(symbol, year, quarter)` on financial_ratios, balance_sheet, income_statement, cash_flow_statement | Speeds up per-symbol annual data lookup |
| P3 | Add `BOND` and `DELISTED` to `exchanges` master table OR clean up orphan stock_exchange rows | Fixes FK semantic integrity |
| P3 | Drop redundant indexes: `idx_stocks_ticker`, `idx_company_overview_symbol` | Minor write performance improvement |
| P4 | Investigate 7,953 close=0 rows — flag for exclusion in backtest/screening queries | Prevents zero-price distortions in analysis |
| P4 | Fix 7 high < low OHLC rows | Minor data correctness |

---

## Unresolved Questions

1. Are the `data_json` differences in duplicate balance_sheet rows meaningful (genuine data updates) or noise from API re-fetching with slightly different precision? If the latter, dedup is safe. If the former, need versioning strategy.
2. The 116 symbols with `close=0` — are they all suspended/halted stocks, or is this a data feed issue for some? PMC appears to be a genuine trading halt case but 116 symbols warrants review.
3. `financial_reports` table is 0 rows but has 4 indexes — is this table still in use or can it be dropped?
4. `stock_price_history` sqlite_sequence shows 6,103,276 — what is the actual `MAX(id)` in the table? The row count is 4,823,384 but max ID may be higher if rows were deleted.
