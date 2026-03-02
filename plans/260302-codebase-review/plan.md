# PE5Y Fund — Codebase Review Report

**Date:** 2026-03-02
**Scope:** Full system — backend, frontend, database, architecture
**Reviewed by:** 4 parallel subagents (scout, backend-reviewer, frontend-reviewer, db-auditor)

---

## Executive Summary

| Area | Critical | High | Medium | Low | Status |
|------|----------|------|--------|-----|--------|
| Database | 2 | 1 | 3 | 2 | Needs immediate attention |
| Backend | 1 | 5 | 10 | 7 | Stable but has correctness issues |
| Frontend | 0 | 4 | 7 | 7 | Clean, minor cleanup needed |
| **Total** | **3** | **10** | **20** | **16** | |

**Overall:** Codebase is well-structured (7,373 LOC production code). Zero SQL injection, zero XSS, zero exposed secrets. Main risks are **database duplicate rows (~321K excess, ~640MB)** and **one unreachable API endpoint**.

---

## Phase 1 — Database Critical Fixes (P0)

**Status:** NOT STARTED
**Details:** [phase-01-database-critical.md](phase-01-database-critical.md)

- CRIT-DB1: 321K duplicate rows in financial statement tables (NULL quarter bypasses UNIQUE)
- CRIT-DB2: sqlite_sequence table corruption (duplicate seq entries)
- HIGH-DB1: Missing composite indexes on financial tables (slow queries)

## Phase 2 — Backend Critical + High Fixes (P1)

**Status:** NOT STARTED
**Details:** [phase-02-backend-fixes.md](phase-02-backend-fixes.md)

- CRIT-BE1: `GET /api/verify/batch/check` permanently unreachable (route order bug)
- HIGH-BE1: N+1 DB connections in update_prices_stream (700+ open/close cycles)
- HIGH-BE2: No WAL mode → concurrent writes produce "database is locked"
- HIGH-BE3: `_to_int()` returns 0 → 1970-01-01 bars silently inserted
- HIGH-BE4: IndexError if VCI OHLCV arrays have mismatched length
- HIGH-BE5: readonly connect silently falls back to writable

## Phase 3 — Frontend High + Medium Fixes (P2)

**Status:** NOT STARTED
**Details:** [phase-03-frontend-fixes.md](phase-03-frontend-fixes.md)

- HIGH-FE1: Missing useEffect cleanup in portfolio (stale state updates)
- HIGH-FE2: Missing SSE stream cleanup on unmount in data page
- HIGH-FE3: SSE abort indistinguishable from network error
- HIGH-FE4: clipboard.writeText not awaited (silent failure)
- MED-FE1: pct URL param not validated
- MED-FE2: Progress bar can overflow 100%
- MED-FE3: Global CSS transition conflicts with Tailwind utilities

## Phase 4 — Medium/Low Cleanup (P3)

**Status:** NOT STARTED
**Details:** [phase-04-cleanup.md](phase-04-cleanup.md)

- Backend: DRY violations (_dec() duplicated), dead config fields, hardcoded paths
- Frontend: formatVND duplicated, missing aria labels, no error boundary
- Database: orphaned records, redundant indexes, close=0 anomalies
