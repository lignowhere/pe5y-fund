# Code Standards

> Last updated: 2026-03-04

## General Principles

- **YAGNI** — You Aren't Gonna Need It
- **KISS** — Keep It Simple, Stupid
- **DRY** — Don't Repeat Yourself

## File Conventions

- **Naming**: kebab-case for all file names (e.g., `market-cap-filter.py`, `data-routes.py`)
- **Max lines**: 200 per file — split into focused modules when exceeding
- **Composition over inheritance**: prefer small, composable modules

## Python (Fund Backend)

### Structure
```
backend/
├── main.py              # FastAPI app + lifespan + CORS (v0.2.0)
├── config.py            # Frozen dataclasses (AppConfig, StrategyConfig, VCIConfig, KBSConfig)
│                        # + JSON override support (save_strategy_config, reload_config)
├── api/                 # Route modules (APIRouter per domain)
├── data/                # External API clients + data pipeline
├── strategy/            # Core strategy logic
│   ├── signal_pe_ttm_20q.py  # PE_TTM_20Q signal (PRODUCTION)
│   ├── signal.py             # PE5Y signal (reference only)
│   ├── optimizer.py          # Uses generate_signal_20q
│   ├── position_sizer.py     # pe_ratio field (renamed from pe_5y_avg)
│   ├── market_cap_filter.py  # Dynamic market cap floor
│   ├── benchmark.py          # VNINDEX comparison
│   └── ktpl_adjustment.py    # Welfare fund leakage (tested, rejected)
├── backtest/            # Cash flow simulation + real data backtest
│   ├── cashflow_real.py      # Uses generate_signal_20q
│   ├── sensitivity_runner.py # 72-run sweep; save_for_optimizer()
│   ├── capital_deployment_sim.py  # DCA comparison
│   ├── run_comparison.py     # CLI runner
│   └── run_deployment.py     # CLI runner
├── report/              # Report generation
│   └── pdf_report.py         # PDF strategy comparison
├── database/            # SQLite helpers (context managers)
└── scheduler/           # APScheduler background jobs
```

### Patterns
- **Configuration**: Frozen dataclasses with env-var defaults (`@dataclass(frozen=True)`); runtime JSON overrides via `strategy_config.json` (gitignored, adjacent to DB)
- **Database access**: Context managers (`connect()` read-only, `connect_rw()` read-write)
- **API clients**: Class-based with rate limiting (`_throttle()` method, `__enter__/__exit__`)
- **Route organization**: One `APIRouter` per domain, prefixed (e.g., `/api/strategy`)
- **Type hints**: Use `from __future__ import annotations`, `Optional`, union types
- **Error handling**: FastAPI `HTTPException` for API errors, `try/except` with logging for background tasks
- **Imports**: Relative imports within package (`from ..config import get_config`)
- **DB path**: Always resolved from `PE5Y_DB_PATH` env var; default `./vietnam_stocks.db` (local, gitignored)

### Signal File Convention

Two signal files coexist:

| File | Status | Entry point |
|------|--------|-------------|
| `signal_pe_ttm_20q.py` | Production | `generate_signal_20q()` → returns `list[PE20QCandidate]` |
| `signal.py` | Reference only | `generate_signal()` → returns `list[Candidate]` |

`optimizer.py` and `cashflow_real.py` import `generate_signal_20q` from `signal_pe_ttm_20q`.
`signal.py` is NOT imported by production code paths.

### Data Flow
```
VCI/KBS APIs → data clients → updater → SQLite
SQLite → signal_pe_ttm_20q.py → optimizer.py → position_sizer.py → API response
```

### SQL Style
- Raw SQL via `sqlite3` (no ORM for fund backend — performance-critical queries)
- Parameterized queries (no f-strings with user input)
- `GROUP BY + AVG` to handle duplicate rows
- `GLOB '[A-Z][A-Z][A-Z]'` for 3-letter stock symbol filtering
- Quarterly EPS: `WHERE quarter IS NOT NULL AND quarter BETWEEN 1 AND 4`
- Annual EPS: `WHERE quarter IS NULL`

### Backtest Scripts
- `cashflow_real.py` must set `os.environ.setdefault("PE5Y_DB_PATH", "./vietnam_stocks.db")` before imports
- Use relative DB path; no hardcoded absolute Windows paths
- `sensitivity_runner.py`: call `save_for_optimizer()` after sweep to write JSON for API heatmap

## TypeScript/Node.js (Inventory Backend)

### Structure
```
backend/src/
├── index.ts             # Express app setup
├── config/              # Prisma client singleton
├── middleware/           # Error handler, async handler, validation
├── products/            # Product domain (controller/service/repo/routes/schemas)
└── warehouses/          # Warehouse domain
```

### Patterns
- **Layered architecture**: Controller → Service → Repository → Prisma
- **Validation**: Zod schemas (parsed in middleware)
- **Error handling**: Custom `AppError` class + centralized error middleware
- **Async**: `asyncHandler` wrapper for route handlers
- **Database**: Prisma ORM with PostgreSQL

## TypeScript/React (Frontend)

### Structure
```
frontend/src/
├── app/                 # Next.js App Router pages
│   ├── layout.tsx       # Root layout with navigation
│   ├── page.tsx         # Dashboard (client component)
│   ├── portfolio/
│   │   ├── page.tsx     # Portfolio table + CSV + sort + RebalanceCalculator
│   │   └── rebalance-calculator.tsx  # Deposit/withdraw cash flow component
│   ├── config/page.tsx  # Live strategy config editor
│   ├── verify/          # Verification page
│   └── data/            # Data management page + update-progress-panel.tsx
└── lib/
    ├── api.ts           # Centralized API client + TypeScript interfaces
    └── format.ts        # Shared formatters (fmtVND, fmtPrice, fillColor)
```

### Patterns
- **Client components**: `"use client"` for interactive pages
- **API client**: Single `api` object with typed methods in `lib/api.ts`
- **Shared formatters**: Extract reusable display functions to `lib/format.ts` (DRY)
  - `fmtVND(v)` — formats VND value as T/B/M suffix
  - `fmtPrice(v)` — formats price in thousands (e.g. `25.3k`)
  - `fillColor(rate)` — returns Tailwind class string for fill rate coloring
- **Styling**: Tailwind CSS 4 with dark mode support (`dark:` variants)
- **State**: React `useState` (no external state management)
- **SSE streaming**: `_streamSSE` DRY helper in `api.ts`, returns `AbortController`
- **Language**: Vietnamese UI labels (e.g., "Vốn", "Phân tích", "Khuyến nghị", "Nạp/Rút")
- **Rebalance Calculator**: Collapsible `<details>` component; diffs old vs new capital positions
- **Field name**: `pe_ratio` (not `pe_5y_avg`) in all TypeScript interfaces and UI columns

### Rebalance Calculator Pattern
```typescript
// RebalanceTrade interface (api.ts)
interface RebalanceTrade {
  symbol: string;
  old_shares: number;   // target_shares at old capital
  new_shares: number;   // target_shares at new capital
  trade_shares: number; // positive = buy, negative = sell
  trade_value_vnd: number;
}

// Logic: fetch new portfolio at newCapital, diff against currentData.positions
const newData = await api.portfolio(newCapital, pct, year);
const trades = computeTrades(currentData.positions, newData.positions);
```

## Cloudflare Workers (SEO Automation)

### Structure
```
seo-automation/worker/src/
├── index.ts             # Hono app + scheduled handler
├── analyzers/           # SEO analysis modules
├── clients/             # Crawler implementations
├── services/            # AI rewrite, SEO analysis, DB
└── durable-objects/     # Per-site crawler coordination
```

### Patterns
- **Framework**: Hono (lightweight, Workers-native)
- **Database**: Cloudflare D1 (SQLite-compatible)
- **Coordination**: Durable Objects for per-site state
- **Crawling**: Hybrid strategy (simple fetch → CF Browser fallback)

## Naming Conventions

| Item | Convention | Example |
|------|-----------|---------|
| Files | kebab-case | `market-cap-filter.py`, `rebalance-calculator.tsx` |
| Python modules | snake_case | `signal_pe_ttm_20q.py`, `vci_client.py` |
| Python classes | PascalCase | `PE20QCandidate`, `VCIClient`, `ConfigResult` |
| Python functions | snake_case | `generate_signal_20q()`, `select_top_n()`, `calc_benchmark_cagr()` |
| Python constants | UPPER_SNAKE | `CLOSE_SCALE_VND`, `MIN_QUARTERS` |
| TypeScript files | kebab-case | `product.controller.ts`, `rebalance-calculator.tsx` |
| TypeScript interfaces | PascalCase | `ConfigResult`, `Position`, `RebalanceTrade` |
| React components | PascalCase | `RebalanceCalculator`, `PortfolioContent` |
| API routes | kebab-case URLs | `/api/data/update/prices/stream` |
| DB columns | snake_case | `quantity_available`, `created_at`, `trade_value_vnd` |
| Prisma models | PascalCase | `Product`, `StockTransfer` |
| Frontend functions | camelCase | `fmtVND`, `fmtPrice`, `fillColor`, `computeTrades` |
| PE ratio field | snake_case | `pe_ratio` (API response and TypeScript interface) |

## Gitignore Rules

Key entries in root `.gitignore`:
- `*.db`, `*.db-wal`, `*.db-shm` — SQLite database files
- `.env`, `.env.local`, `.env.production` — environment secrets
- `__pycache__/`, `*.pyc` — Python bytecode
- `node_modules/` — Node.js dependencies
- `strategy_config.json` — runtime strategy overrides

## Commit Standards

- Conventional commit format: `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`
- No AI references in commit messages
- Focused commits (one logical change per commit)
- Never commit `.env`, API keys, credentials, or `*.db` files
