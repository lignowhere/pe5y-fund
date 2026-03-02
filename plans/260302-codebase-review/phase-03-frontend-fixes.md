# Phase 3 — Frontend High + Medium Fixes

**Priority:** P2
**Scope:** frontend/src/ (1,351 LOC, Next.js 16, React 19, TypeScript strict)
**Build:** PASSES | TypeScript: PASSES | npm audit: 0 vulnerabilities

---

## HIGH-FE1: Missing useEffect Cleanup in Portfolio Page

**File:** `frontend/src/app/portfolio/page.tsx:66-80`

No abort controller on fetch. If user navigates away mid-fetch, `.then(setData)` fires on unmounted component. Also triggers ESLint `react-hooks/set-state-in-effect` error.

**Fix:** Add AbortController + guard in useEffect.

---

## HIGH-FE2: Missing SSE Stream Cleanup on Unmount

**File:** `frontend/src/app/data/page.tsx:102`

Neither `priceAbortRef` nor `finAbortRef` is aborted when Data page unmounts. `onDone` callback fires post-unmount.

**Fix:** Add cleanup useEffect:
```tsx
useEffect(() => {
  return () => {
    priceAbortRef.current?.abort();
    finAbortRef.current?.abort();
  };
}, []);
```

---

## HIGH-FE3: SSE Abort Indistinguishable from Network Error

**File:** `frontend/src/lib/api.ts:202-204`

`.catch(() => { onDone(); })` swallows all errors including AbortError. User cancel looks identical to network failure.

**Fix:** Check `err.name === "AbortError"` before calling onDone.

---

## HIGH-FE4: Clipboard Write Not Awaited

**File:** `frontend/src/app/portfolio/page.tsx:39`

`navigator.clipboard.writeText()` is not awaited/caught. On non-HTTPS or denied permission, fails silently but shows "Copied!".

**Fix:** Make async, add try/catch with user feedback.

---

## Medium Issues

| ID | Issue | File |
|----|-------|------|
| M1 | `pct` URL param not validated (NaN passes to API) | portfolio/page.tsx:57 |
| M2 | Progress bar can overflow 100% if server sends current>total | update-progress-panel.tsx:91 |
| M3 | Global `*` CSS transition conflicts with Tailwind utilities | globals.css:29-35 |
| M4 | Array index as key for growing log list | update-progress-panel.tsx:116 |
| M5 | `mapSSEtoProgress` creates new array on every event (spread) | data/page.tsx:41-71 |
| M6 | `parseInt(year)` missing radix argument | verify/page.tsx:54 |
| M7 | `sorted()` called twice per render in portfolio | portfolio/page.tsx:204,246 |

---

## Low Issues

| ID | Issue | File |
|----|-------|------|
| L1 | No error boundary in component tree | layout.tsx |
| L2 | Missing ARIA labels on spinners, sort buttons, status dots | multiple |
| L3 | No active link indicator in nav | layout.tsx:29-34 |
| L4 | `formatVND`/`fmtVND` duplicated across 2 files | page.tsx, portfolio/page.tsx |
| L5 | API_BASE fallback hardcoded, no .env.example | api.ts:1 |
| L6 | Next.js pinned to exact version (no ^) | package.json |
| L7 | Progress bar width needs Math.min guard | update-progress-panel.tsx:91 |

---

## Positive Observations

- 100% type coverage (strict mode, zero `any`)
- Zero `dangerouslySetInnerHTML` — no XSS vectors
- Zero exposed secrets
- Clean SSE streaming with DRY `_streamSSE` helper
- Static Tailwind class map avoids purge issues
- `Suspense` boundary correctly wrapping `useSearchParams()`
- Build succeeds in 1.6s with Turbopack
