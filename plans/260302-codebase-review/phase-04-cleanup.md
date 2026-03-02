# Phase 4 — Medium/Low Cleanup

**Priority:** P3
**Scope:** Codebase-wide quality improvements

---

## Backend Cleanup

| Item | Action | File |
|------|--------|------|
| DRY: `_dec()` duplicated | Extract to shared `backend/data/utils.py` | vci_client.py, kbs_client.py |
| Dead config: KBSConfig.base_url/price_url never read | Remove or wire into KBSClient | config.py, kbs_client.py |
| Hardcoded path | Replace `D:/AI/baocaotaichinh/...` with config | optimizer.py:108 |
| Unused import | Remove `asynccontextmanager` | scheduler/__init__.py:5 |
| Stub endpoint | Return 501 instead of 200 | strategy_routes.py:115 |
| Deep copy overhead | Use `copy.deepcopy()` instead of json roundtrip | vci_client.py:111 |
| SSE done event | Yield `{type:"done"}` after error event | data_routes.py:120 |
| count_back bound | Add max(count_back, 365) validation | data_routes.py:88 |
| fix_integer_timestamps import | Remove unused import from data_routes.py | data_routes.py:18 |

## Frontend Cleanup

| Item | Action | File |
|------|--------|------|
| Extract formatVND | Create `src/lib/format.ts` | page.tsx, portfolio/page.tsx |
| Add error boundary | Wrap in layout.tsx | layout.tsx |
| Nav active state | Use `usePathname()` for active class | layout.tsx |
| Accessibility | Add role/aria-label to spinners/sort headers | multiple |
| useCallback handlers | Wrap update handlers | data/page.tsx |
| useMemo sorted | Memoize sorted positions | portfolio/page.tsx |
| CSS transition scope | Remove global `*` transition or scope to body | globals.css |

## Database Cleanup

| Item | Action |
|------|--------|
| Drop redundant indexes | `idx_stocks_ticker`, `idx_company_overview_symbol` |
| Clean orphan stock_exchange | Remove 1,449 bond entries or add BOND to exchanges |
| Flag close=0 symbols | Exclude 116 suspended symbols from backtesting |
| Drop empty table | `financial_reports` (0 rows, 4 dead indexes) |
| VACUUM | After dedup, recover ~640MB |
