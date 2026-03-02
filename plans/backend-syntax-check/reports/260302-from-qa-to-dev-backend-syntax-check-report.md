# Backend Syntax & Signature Verification Report
**Date:** 2026-03-02
**Scope:** `D:\AI\pe5y-fund\backend` — all `.py` files

---

## Test Results Overview

| Check | Result |
|---|---|
| Files checked | 26 |
| Syntax PASSED | 26 |
| Syntax FAILED | 0 |
| pytest tests found | 0 (tests dir is empty stub) |
| Build/import errors | 0 |

---

## Syntax Check (ast.parse) — ALL 26 FILES PASSED

```
backend/__init__.py                      OK
backend/api/__init__.py                  OK
backend/api/data_routes.py               OK
backend/api/strategy_routes.py           OK
backend/api/verify_routes.py             OK
backend/backtest/__init__.py             OK
backend/backtest/cashflow_real.py        OK
backend/backtest/cashflow_sim.py         OK
backend/config.py                        OK
backend/data/__init__.py                 OK
backend/data/financial_updater.py        OK
backend/data/kbs_client.py               OK
backend/data/updater.py                  OK
backend/data/vci_client.py               OK
backend/data/verifier.py                 OK
backend/database/__init__.py             OK
backend/database/connection.py           OK
backend/main.py                          OK
backend/models/__init__.py               OK
backend/scheduler/__init__.py            OK
backend/strategy/__init__.py             OK
backend/strategy/market_cap_filter.py    OK
backend/strategy/optimizer.py            OK
backend/strategy/position_sizer.py       OK
backend/strategy/signal.py               OK
backend/tests/__init__.py                OK
```

---

## Function Signature vs Call Site Verification — ALL MATCH

### 1. `fix_integer_timestamps`

**Definition** (`backend/data/updater.py:23`):
```python
def fix_integer_timestamps(db_path: Path) -> int:
```

**Call sites:**
- `main.py:25` — `fix_integer_timestamps(_cfg.db_path)` — MATCH
- `data_routes.py` imports it correctly from `..data.updater`

---

### 2. `detect_missing_prices`

**Definition** (`backend/data/updater.py:96`):
```python
def detect_missing_prices(db_path, min_trading_day_gap=3, max_stale_market_days=30)
```

**Call sites (all pass only `db_path`, using defaults):**
- `data_routes.py:50` — `detect_missing_prices(_cfg.db_path)` — MATCH (defaults apply)
- `data_routes.py:71` — `detect_missing_prices(_cfg.db_path)` — MATCH
- `data_routes.py:90` — `detect_missing_prices(_cfg.db_path)` — MATCH
- `data_routes.py:118` — `detect_missing_prices(_cfg.db_path)` — MATCH
- `updater.py:394` — `detect_missing_prices(db_path)` — MATCH (self-call in `get_data_status`)
- `updater.py:485` — `detect_missing_prices(db_path)` — MATCH (self-call in `get_db_health`)

---

### 3. `update_prices_stream`

**Definition** (`backend/data/updater.py:287`):
```python
def update_prices_stream(db_path, symbols, vci, kbs=None, count_back=30) -> Iterator[SymbolProgress]:
```

**Call site** (`data_routes.py:104`):
```python
update_prices_stream(_cfg.db_path, symbols, vci, kbs, count_back)
```
- 5 positional args: `db_path`, `symbols`, `vci`, `kbs`, `count_back` — MATCH
- `kbs` and `count_back` passed positionally (both have defaults; positional is valid) — OK

---

### 4. `update_financials_stream`

**Definition** (`backend/data/financial_updater.py:34`):
```python
def update_financials_stream(db_path, symbols, vci, kbs=None, target_year=None) -> Iterator[FinancialProgress]:
```

**Call site** (`data_routes.py:148`):
```python
update_financials_stream(_cfg.db_path, symbols, vci, kbs, target_year=resolved_year)
```
- 4 positional + 1 keyword — MATCH
- `resolved_year` defined at `data_routes.py:132` (same outer function scope, accessible via closure) — OK
- `target_year is not None` guard in `financial_updater.py:122,128` — correct usage

---

## Import Chain Verification

| Symbol | Defined in | Imported by | Status |
|---|---|---|---|
| `fix_integer_timestamps` | `updater.py:23` | `data_routes.py` (line 18), `main.py` (line 14) | OK |
| `update_prices_stream` | `updater.py:287` | `data_routes.py` (line 22) | OK |
| `update_financials_stream` | `financial_updater.py:34` | `updater.py` re-exports via `from .financial_updater import ... # noqa: F401`, also imported directly in `data_routes.py:24` | OK |
| `detect_missing_prices` | `updater.py:96` | `data_routes.py` (line 16) | OK |

---

## Pytest

No test files found in `backend/tests/` (directory contains only `__init__.py`). pytest 8.4.2 is available. No tests ran.

---

## Critical Issues

None.

---

## Recommendations

1. `backend/tests/` is an empty stub — add unit tests for the new functions:
   - `fix_integer_timestamps()` — test idempotency (safe to call twice)
   - `detect_missing_prices()` with `max_stale_market_days` — test stale symbol exclusion
   - `_ts_to_date()` — test UTC handling, numeric strings, None inputs
   - `_insert_bars()` / `_latest_bar_date()` — unit test with mock DB
2. Consider a test for the KBS fallback logic in `update_prices_stream` (stale-VCI-data path).

---

## Unresolved Questions

None.
