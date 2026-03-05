# Portfolio Comparison Feature - Codebase Scout Report

**Date**: 2026-03-04  
**Task**: Understand codebase for implementing portfolio comparison feature (current price vs. initial buy price at rebalance)

## Executive Summary

The PE_TTM_20Q system stores prices in **thousands of VND** (scaled) in SQLite. Rebalance dates (Sep 1) and buy prices are already tracked in the signal generation layer. Frontend portfolio UI currently shows current positions only—no gain/loss comparison exists yet.

---

## Key Findings

### 1. Price Storage & Scaling

**File**: `D:\AI\pe5y-fund\backend\data\updater.py` (lines 22-35)

- All prices in `stock_price_history` table stored in **thousands of VND**
- Frontend formats with `.toFixed(1) + "k"` (e.g., 66.6k)
- When computing values, multiply by `CLOSE_SCALE_VND = 1000.0` to get actual VND

**Dividend Adjustment**:
- VCI API payload includes `"dividend"` field but is **NOT stored** in financial_updater.py
- Prices are raw OHLCV, not adjusted for dividends/splits
- No dividend adjustment logic exists currently

### 2. Rebalance Date & Buy Price Logic

**Files**: 
- `D:\AI\pe5y-fund\backend\api\strategy_routes.py` (lines 52-82)
- `D:\AI\pe5y-fund\backend\strategy\signal_pe_ttm_20q.py` (lines 36-120)

Key points:
- Rebalance date hardcoded to Sep 1 of hold_year
- `generate_signal_20q()` captures buy prices when `rebalance_date` provided
- `_query_price_on_or_after()` fetches first price on/after Sep 1
- `PE20QCandidate` dataclass includes `buy_price_vnd` field

**Finding**: Buy price logic **already implemented** in backend but NOT exposed in portfolio API response.

### 3. Current Portfolio API Response Missing Fields

**File**: `D:\AI\pe5y-fund\backend\api\strategy_routes.py` (lines 95-109)

Current `Position` response lacks:
- `buy_price_vnd` (available in `PE20QCandidate` but not mapped)
- `rebalance_date` for context

### 4. Position Sizer - Current Prices

**File**: `D:\AI\pe5y-fund\backend\strategy\position_sizer.py`

- `_query_latest_prices()` fetches **latest close** for each symbol
- Uses `MAX(time)` to get most recent date
- Correctly scales prices in thousands VND

### 5. Frontend Portfolio Page

**File**: `D:\AI\pe5y-fund\frontend\src\app\portfolio\page.tsx`

Current UI shows: symbol, PE, price, shares, value, ADV, days, fill_rate
**Missing**: initial buy price, gain/loss, gain percentage

### 6. Frontend API Layer Types

**File**: `D:\AI\pe5y-fund\frontend\src/lib/api.ts` (lines 48-59)

Position interface missing:
- `buy_price_vnd`
- `gain_vnd`
- `gain_pct`
- `initial_value_vnd`

---

## Database Schema

**stock_price_history**:
- symbol (TEXT)
- time (TEXT YYYY-MM-DD)
- open/high/low/close (REAL, thousands VND)
- volume (INTEGER, shares)

**financial_ratios**:
- symbol, year, quarter, eps_vnd, pe_ratio, etc.

No schema changes needed—prices already scaled correctly.

---

## Implementation Architecture

### Current (Broken) Flow:
```
generate_signal_20q() [has buy_price_vnd ✓]
  ↓
size_portfolio() [loses buy_price_vnd ✗]
  ↓
API Response [no buy_price ✗]
  ↓
Frontend [cannot compute gain ✗]
```

### Fixed Flow:
```
generate_signal_20q() [has buy_price_vnd ✓]
  ↓
size_portfolio() + PositionTarget [ADD buy_price_vnd field]
  ↓
API Response [include buy_price_vnd]
  ↓
Frontend computes:
  - gain_vnd = (current_price - buy_price) * shares
  - gain_pct = (current_price - buy_price) / buy_price * 100
  - current_value = current_price * shares
  - initial_value = buy_price * shares
```

---

## Files Summary

### Backend (Python/FastAPI)

| File | Purpose | Action |
|------|---------|--------|
| `backend/api/strategy_routes.py` | Portfolio endpoint | Map buy_price from signal to response |
| `backend/strategy/signal_pe_ttm_20q.py` | Signal + buy price | Already captures buy price ✓ |
| `backend/strategy/position_sizer.py` | Position sizing | Add buy_price_vnd to PositionTarget |
| `backend/data/vci_client.py` | VCI data fetch | Has dividend field (not extracted) |
| `backend/database/connection.py` | DB helpers | Already good |

### Frontend (Next.js/React/TypeScript)

| File | Purpose | Action |
|------|---------|--------|
| `frontend/src/lib/api.ts` | API types | Extend Position with gain fields |
| `frontend/src/app/portfolio/page.tsx` | Main portfolio UI | Add comparison columns |
| `frontend/src/lib/format.ts` | Format utils | Already supports prices |
| `frontend/src/app/portfolio/rebalance-calculator.tsx` | Rebalance UI | Optional: show gains |

---

## Dividend Adjustment Status

**Current**: VCI API returns raw OHLCV (no adjustment)

**To support dividend-adjusted prices**:
1. Extract dividend field from VCI API (already in payload)
2. Create table: dividend_payments (symbol, date, amount)
3. Implement get_dividend_adjusted_price() function
4. Apply when computing comparisons

**For MVP**: Use raw prices; note gains are "before dividend adjustments"

---

## Data Flow Summary

**Buy Price Capture**:
- ✓ Captured in PE20QCandidate.buy_price_vnd
- ✗ Not exposed in portfolio API

**Rebalance Date Logic**:
- ✓ Correctly identifies Sep 1
- ✓ Fetches prices from that date
- ✓ Matches rebalance month = 9

**Current Price Queries**:
- ✓ Latest close from stock_price_history
- ✓ Correctly scaled (thousands VND)

**Frontend Types**:
- ✗ No buy_price_vnd field
- ✗ No gain fields
- ✗ No initial_value field

---

## Unresolved Questions

1. **Dividend adjustment**: Should comparison be dividend-adjusted or raw/nominal?
   - **Recommendation**: MVP with raw prices

2. **Rebalance gain tracking**: Show realized/unrealized gains in rebalance calculator?
   - **Recommendation**: Extend RebalanceTrade with gain fields

3. **Historical buy prices**: If allocation changes mid-year, track per entry or use latest Sep 1?
   - **Recommendation**: Always use official Sep 1 price for consistency

4. **Multiple rebalances**: Show comparison per cycle or aggregate?
   - **Recommendation**: Show current year; archive priors separately

---

## Recommended Implementation Steps

1. **Backend**:
   - Add buy_price_vnd to PositionTarget dataclass
   - Modify size_portfolio() to map buy_price from signals
   - Update /api/strategy/portfolio to include buy_price_vnd

2. **Frontend**:
   - Extend Position interface with: buy_price_vnd, gain_vnd, gain_pct, initial_value_vnd
   - Add columns: Buy Price, Current Price, Initial Value, Current Value, Gain VND, Gain %
   - Compute gains client-side

3. **Testing**:
   - Verify buy prices match Sep 1 price in DB
   - Spot-check gain calculations vs. Excel
   - Test across multiple years

---

**Report Generated**: 2026-03-04
