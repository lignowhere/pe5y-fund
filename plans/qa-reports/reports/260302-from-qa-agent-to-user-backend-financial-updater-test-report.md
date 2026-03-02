# QA Report: Backend Financial Updater Changes
Date: 260302
Agent: qa-agent
Task: Verify `target_year` parameter addition compiles and passes tests

---

## Test Results Overview

| Category | Result |
|---|---|
| Pytest tests collected | 0 |
| Tests passed | 0 |
| Tests failed | 0 |
| Syntax checks | 9/9 PASS |
| Signature/call-site AST checks | PASS |

**No pytest test files exist in `backend/tests/`.** The directory contains only `__init__.py` (empty). Pytest exits with code 5 ("no tests collected"), not a failure.

---

## Syntax Check Results

All 9 backend Python files parsed without errors (Python 3.12.4, `ast.parse`):

| File | Result |
|---|---|
| `backend/data/financial_updater.py` | SYNTAX OK |
| `backend/api/data_routes.py` | SYNTAX OK |
| `backend/data/vci_client.py` | SYNTAX OK |
| `backend/data/kbs_client.py` | SYNTAX OK |
| `backend/data/updater.py` | SYNTAX OK |
| `backend/api/strategy_routes.py` | SYNTAX OK |
| `backend/api/verify_routes.py` | SYNTAX OK |
| `backend/main.py` | SYNTAX OK |
| `backend/config.py` | SYNTAX OK |

---

## Change Verification (AST Analysis)

### 1. `backend/data/financial_updater.py` — `update_financials_stream()` signature

Confirmed params via AST:
```
db_path: Path          (required)
symbols: list[str]     (required)
vci: VCIClient         (required)
kbs: Optional[KBSClient] = None
target_year: Optional[int] = None   <-- NEW PARAM, correctly typed and defaulted
```

### 2. `backend/api/data_routes.py` — call site in `stream_financials_update()`

Confirmed call:
```python
positional: [_cfg.db_path, symbols, vci, kbs]
keyword:    target_year=resolved_year
```

`resolved_year` is assigned before the call:
```python
resolved_year = year if year is not None else datetime.date.today().year - 1
```

Positional args align correctly with the function signature. No argument count mismatch.

---

## Logic Notes

Two conditionals use `target_year` with truthiness checks:
- Line 122: `if target_year and row.year == target_year`
- Line 128: `if target_year and not has_target_year`

Using `and` (truthy check) means `target_year=0` would be treated as "not set". Year 0 is not a valid financial year, so this is a non-issue for production. If the API ever allows `year=0` as input, it would silently fall through as if `target_year=None`.

---

## Build Status

No build process for this Python project. Dependencies in `requirements.txt` are: `fastapi`, `uvicorn`, `httpx`, `pydantic`, `apscheduler`. All standard packages; no compilation step.

---

## Critical Issues

None. Both changed files compile cleanly and the call site matches the new signature.

---

## Recommendations

1. **Add pytest tests for `update_financials_stream()`** — zero test coverage exists for this function. Minimum needed:
   - `target_year=None` (default): all inserted rows counted as "ok"
   - `target_year=2024` with rows including 2024: status="ok"
   - `target_year=2024` with rows only for 2023: status="skip", skip_reason contains "no 2024 data yet"
   - `target_year=2024`, no rows returned from VCI/KBS: status="skip"
   - Exception from `vci.get_annual_ratios`: status="error"

2. **Guard `target_year=0`** — change line 122/128 checks from `if target_year` to `if target_year is not None` to be semantically precise.

3. **Add `conftest.py`** to `backend/tests/` with a SQLite in-memory fixture and mock `VCIClient`/`KBSClient` stubs.

---

## Unresolved Questions

- Is `backend/tests/` intentionally empty (no unit tests planned yet), or were tests accidentally omitted from the commit?
- Does the project have a CI pipeline that should gate on test execution?
