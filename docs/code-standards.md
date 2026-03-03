# Code Standards

> Last updated: 2026-03-03

## General Principles

- **YAGNI** — You Aren't Gonna Need It
- **KISS** — Keep It Simple, Stupid
- **DRY** — Don't Repeat Yourself

## File Conventions

- **Naming**: kebab-case for all file names (e.g., `market-cap-filter.py`, `data-routes.py`)
- **Max lines**: 200 per file — split into focused modules when exceeding
- **Composition over inheritance**: prefer small, composable modules

## Python (PE5Y Backend)

### Structure
```
backend/
├── main.py              # FastAPI app + lifespan + CORS
├── config.py            # Frozen dataclasses (AppConfig, StrategyConfig, VCIConfig, KBSConfig)
│                        # + JSON override support (save_strategy_config, reload_config)
├── api/                 # Route modules (APIRouter per domain)
├── data/                # External API clients + data pipeline
├── strategy/            # Core PE5Y logic (signal, optimizer, sizer, benchmark)
├── backtest/            # Cash flow simulation + real data backtest
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

### Data Flow
```
VCI/KBS APIs → data clients → updater → SQLite
SQLite → signal.py → optimizer.py → position_sizer.py → API response
```

### SQL Style
- Raw SQL via `sqlite3` (no ORM for PE5Y — performance-critical queries)
- Parameterized queries (no f-strings with user input)
- `GROUP BY + AVG` to handle duplicate rows
- `GLOB '[A-Z][A-Z][A-Z]'` for 3-letter stock symbol filtering

### Backtest Scripts
- `cashflow_real.py` must set `os.environ.setdefault("PE5Y_DB_PATH", "./vietnam_stocks.db")` before imports
- Use relative DB path; no hardcoded absolute Windows paths

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
| Python modules | snake_case | `signal.py`, `vci_client.py` |
| Python classes | PascalCase | `PE5YCandidate`, `VCIClient`, `ConfigResult` |
| Python functions | snake_case | `generate_signal()`, `select_top_n()`, `calc_benchmark_cagr()` |
| Python constants | UPPER_SNAKE | `CLOSE_SCALE_VND`, `SEED` |
| TypeScript files | kebab-case | `product.controller.ts`, `rebalance-calculator.tsx` |
| TypeScript interfaces | PascalCase | `ConfigResult`, `Position`, `RebalanceTrade` |
| React components | PascalCase | `RebalanceCalculator`, `PortfolioContent` |
| API routes | kebab-case URLs | `/api/data/update/prices/stream` |
| DB columns | snake_case | `quantity_available`, `created_at`, `trade_value_vnd` |
| Prisma models | PascalCase | `Product`, `StockTransfer` |
| Frontend functions | camelCase | `fmtVND`, `fmtPrice`, `fillColor`, `computeTrades` |

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
