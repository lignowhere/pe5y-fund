# Phase 1 — Database Critical Fixes

**Priority:** P0 — Immediate
**DB:** vietnam_stocks.db (1.53 GB, SQLite 3.50.4, WAL mode)
**Integrity:** `PRAGMA integrity_check` = OK

---

## CRIT-DB1: 321K Duplicate Rows in Financial Statement Tables

**Root cause:** `UNIQUE(symbol, period, year, quarter)` — SQLite treats `NULL != NULL` for quarter column, so annual records (quarter=NULL) bypass the constraint entirely. Every re-fetch inserts a new row.

| Table | Total Rows | Distinct Keys | Excess Rows |
|-------|-----------|---------------|-------------|
| balance_sheet | 180,419 | 73,485 | 106,934 |
| income_statement | 180,615 | 73,493 | 107,122 |
| cash_flow_statement | 180,681 | 73,462 | 107,219 |
| financial_ratios | 74,709 | 74,229 | 480 |
| **Total** | | | **321,755** |

**Fix:**
```sql
-- Step 1: Dedup — keep most recent row per group
DELETE FROM balance_sheet WHERE id NOT IN (
  SELECT MAX(id) FROM balance_sheet GROUP BY symbol, period, year, COALESCE(quarter, -1)
);
-- Repeat for income_statement, cash_flow_statement, financial_ratios

-- Step 2: Add partial unique indexes to prevent recurrence
CREATE UNIQUE INDEX IF NOT EXISTS uq_bs_annual ON balance_sheet(symbol, period, year) WHERE quarter IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_bs_quarterly ON balance_sheet(symbol, period, year, quarter) WHERE quarter IS NOT NULL;
-- Repeat for other 3 tables
```

**Caveat:** 13,871/14,061 duplicate groups have different `data_json` (API values changed between fetches). Keep `MAX(id)` = most recent fetch.

---

## CRIT-DB2: sqlite_sequence Table Corruption

6 AUTOINCREMENT tables have duplicate rows in sqlite_sequence:

| Table | Seq Values | Risk |
|-------|-----------|------|
| stock_price_history | 6,103,276 AND 5,901,754 | HIGH — lower value could cause ID collision |
| financial_ratios | 97,329 AND 95,973 | HIGH — same risk |
| balance_sheet | 276,454, 276,454 | Low — identical values |
| cash_flow_statement | 274,558, 274,558 | Low — identical |
| income_statement | 274,545, 274,545 | Low — identical |
| update_log | 885, 885 | Low — identical |

**Fix:**
```sql
-- Keep only the highest seq value per table
DELETE FROM sqlite_sequence
WHERE rowid NOT IN (
  SELECT rowid FROM sqlite_sequence s1
  WHERE s1.seq = (SELECT MAX(s2.seq) FROM sqlite_sequence s2 WHERE s2.name = s1.name)
);
```

---

## HIGH-DB1: Missing Composite Indexes

Financial queries use `WHERE symbol=? AND year=? AND quarter IS NULL` but only single-column indexes exist.

**Fix:**
```sql
CREATE INDEX IF NOT EXISTS idx_ratios_sym_yr_qtr ON financial_ratios(symbol, year, quarter);
CREATE INDEX IF NOT EXISTS idx_bs_sym_yr_qtr ON balance_sheet(symbol, year, quarter);
CREATE INDEX IF NOT EXISTS idx_is_sym_yr_qtr ON income_statement(symbol, year, quarter);
CREATE INDEX IF NOT EXISTS idx_cf_sym_yr_qtr ON cash_flow_statement(symbol, year, quarter);
```

---

## Other DB Findings

| Finding | Count | Severity |
|---------|-------|----------|
| Integer timestamps remaining | 0 | FIXED |
| close=0 OHLCV rows | 7,953 (116 symbols) | Medium — suspended stocks |
| high<low invalid OHLC | 7 rows | Low — historical source errors |
| Orphaned price rows (index symbols) | 39,445 (19 symbols) | Low — intentional |
| Orphaned stock_exchange entries | 1,449 (bonds) | Low |
| Redundant indexes (PK duplicates) | 2 | Low — drop candidates |
| financial_reports table (empty) | 0 rows, 4 indexes | Low — dead code |

**Data freshness:** Price data current to 2026-03-02. 1,579 symbols across HSX/HNX/UPCOM.
