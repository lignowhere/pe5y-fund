# Code Review: LAST 8Q+ Strategy Variant

**Date:** 2026-03-05
**Scope:** LAST 8Q+ strategy addition across backend signal, API, optimizer, and frontend

---

## Code Review Summary

### Scope
- Files reviewed: 7
  - `backend/strategy/signal_pe_ttm_20q.py`
  - `backend/api/strategy_routes.py`
  - `backend/strategy/optimizer.py`
  - `frontend/src/lib/api.ts`
  - `frontend/src/app/page.tsx`
  - `frontend/src/app/portfolio/page.tsx`
  - `frontend/src/app/portfolio/rebalance-calculator.tsx`
- Lines analyzed: ~800
- Review focus: LAST 8Q+ strategy variant addition

### Overall Assessment
The implementation is structurally clean and logically correct for the core filtering logic. The strategy dispatch pattern (`STRATEGY_PARAMS` dict + `**strat_kw` unpacking) is DRY and easily extensible. The EPS ordering invariant is sound. One medium-priority bug found in the rebalance calculator (missing `month` propagation). Two low-priority concerns around historical CAGR lookup and input validation guardrails.

---

## Critical Issues
None.

---

## High Priority Findings
None.

---

## Medium Priority Improvements

### M1: RebalanceCalculator loses `month` context — incorrect portfolio comparison
**File:** `frontend/src/app/portfolio/rebalance-calculator.tsx:74`, `frontend/src/app/portfolio/page.tsx:386-391`

`RebalanceCalculator` Props interface has no `month` field. When `handleCalculate` fires, it calls:
```ts
api.portfolio(newCapital, pct, year, undefined, strategy)
//                               ^month = undefined
```
But the parent page calls:
```ts
api.portfolio(cap, parseFloat(pct), undefined, rebalMonth, strategyParam)
//                                             ^month from URL
```
If a user navigated to `/portfolio?month=6&strategy=LAST_8Q_PLUS`, the main portfolio uses month 6, but the rebalance calculator silently falls back to `config.rebalance_month` (default, likely 9). The computed trade deltas are then comparing portfolios sized for different rebalance months — producing incorrect trade recommendations.

**Fix:**
```tsx
// rebalance-calculator.tsx Props
interface Props {
  currentData: PortfolioResult;
  pct: number;
  year?: number;
  month?: number;   // ADD THIS
  strategy?: string;
}

// In handleCalculate:
const newData = await api.portfolio(newCapital, pct, year, month, strategy);

// In portfolio/page.tsx, pass rebalMonth:
<RebalanceCalculator
  currentData={data}
  pct={parseFloat(pct)}
  year={params.get("year") ? parseInt(params.get("year")!) : undefined}
  month={rebalMonth}     // ADD THIS
  strategy={strategyParam}
/>
```

---

## Low Priority Suggestions

### L1: `require_last_n_positive > MIN_QUARTERS` silently empties results
**File:** `backend/strategy/signal_pe_ttm_20q.py:213-215`

The `len(last_n) < require_last_n_positive` guard in `_query_quarterly_eps` is redundant when `N <= 20` (guaranteed by `MIN_QUARTERS` check above it), but becomes a silent stock-eliminator if anyone adds a strategy variant with `require_last_n_positive > 20`. Consider an assertion or docstring constraint:

```python
# At top of _query_quarterly_eps or in generate_signal_20q:
assert require_last_n_positive <= MIN_QUARTERS, \
    f"require_last_n_positive ({require_last_n_positive}) > MIN_QUARTERS ({MIN_QUARTERS})"
```
Current value N=8 < 20: not a live issue, but worth guarding for future strategy additions.

### L2: `LAST_8Q_PLUS` always gets `historical_cagr=None` from optimizer
**File:** `backend/strategy/optimizer.py:126`

`_load_sensitivity_data` hard-filters `strategy != "PE_TTM_20Q_RELAXED"`. Any call with `LAST_8Q_PLUS` strategy gets `historical_cagr=None` for all `ConfigResult` entries. The optimizer then falls through to `max(eligible, key=lambda r: r.historical_cagr or 0)` — all return 0, so the "recommendation" is essentially arbitrary (first eligible by fill rate alone).

This is acceptable while no backtest data for `LAST_8Q_PLUS` exists yet, but the UI will show `historical_cagr=null` and no Alpha row for all configs, which could be confusing to users. Consider:
- A UI note when `historical_cagr` is null for all results: "No historical data for this strategy variant"
- Or a TODO comment in `_load_sensitivity_data` documenting the extension point

### L3: `/optimize` response does not include `strategy` field
**File:** `backend/api/strategy_routes.py:45-64`

`get_portfolio` returns `"strategy": strategy` in the response. `optimize_strategy` does not. `OptimizeResponse` in `api.ts` has `strategy?: string` (optional) — consistent since the frontend uses local state, not the response value. Minor inconsistency that could confuse future API consumers. Either add `"strategy": strategy` to the optimize response, or remove the optional field from `OptimizeResponse`.

### L4: Deployment transition breaks `rebalance_month` in `OptimizeResponse`
**File:** `frontend/src/lib/api.ts:43-48`

`OptimizeResponse.rebalance_month: number` is non-optional. If the backend is updated before the frontend (or vice versa), the missing field causes `res.rebalance_month` to be `undefined`, and `setRebalMonth(undefined)` would propagate into URL construction and `monthName()` calls. In practice this is a transient deploy-order issue, not a persistent bug. Marking as low priority but worth noting for the deployment runbook.

---

## Positive Observations

1. **Strategy dispatch pattern is excellent.** `STRATEGY_PARAMS` dict + `**strat_kw` unpacking is clean, DRY, and trivially extensible. Adding a new strategy is a one-liner in the dict.

2. **EPS ordering invariant is sound.** SQL `ORDER BY symbol, year, quarter` guarantees the `eps_list` is chronological per symbol. `eps_list[-8:]` correctly captures the 8 most recent quarters. The `len(last_n) < require_last_n_positive` guard provides an extra safety net.

3. **Avg EPS guard correctly handles the "turnaround" edge case.** Even with `require_all_positive=False` and 8 positive recent quarters, a stock with severely negative historical quarters cannot pass the `avg_eps <= 0 -> continue` guard at line 218. This is the right semantic: the 20Q average must still be positive.

4. **Backend validation is thorough.** Both `/optimize` and `/portfolio` validate `strategy not in STRATEGY_PARAMS` with a clear 400 error. Month range is validated. Capital positivity is validated.

5. **Frontend default strategy is safe.** `params.get("strategy") || "TTM_20Q"` in portfolio page correctly handles missing/empty URL param, defaulting to the production strategy.

6. **Strategy label display is correct.** Both the dashboard description (`page.tsx:89-91`) and portfolio title (`portfolio/page.tsx:167`) correctly show human-readable labels for each strategy value.

7. **strategy param propagation in dashboard → portfolio URL.** The `Link` href correctly includes `strategy=${strategy}`, ensuring the selected strategy is carried through to the portfolio page.

---

## Recommended Actions

1. **(Medium — fix before production)** Add `month` prop to `RebalanceCalculator` and thread `rebalMonth` through from `portfolio/page.tsx`. Without this, rebalance trade calculations use the wrong month when a non-default month is selected.

2. **(Low)** Add `assert require_last_n_positive <= MIN_QUARTERS` in `_query_quarterly_eps` to prevent silent future regression.

3. **(Low)** Add a UI hint when all `historical_cagr` values are null (LAST_8Q_PLUS case) so users understand the optimizer recommendation is based on fill-rate only, not CAGR.

4. **(Low)** Decide on `strategy` field in `/optimize` response: either add it for consistency with `/portfolio`, or remove `strategy?: string` from `OptimizeResponse` type.

---

## Metrics
- Type Coverage: TypeScript interfaces fully typed for all new fields; `strategy?: string` optional fields are intentional
- Linting Issues: 0 critical, 0 high
- Test Coverage: No unit tests for `require_last_n_positive` filter logic (pre-existing gap)

## Unresolved Questions
- Is there a plan to run backtests for `LAST_8Q_PLUS` and populate `sensitivity-results.json` with its CAGR data? Without it, the optimizer's recommendation for this strategy is not meaningful.
- Is the `N=8` threshold (last 8 quarters) derived from analysis or chosen arbitrarily? Worth documenting the rationale in `STRATEGY_PARAMS` or a docstring.
