# Code Standards

> Last updated: 2026-03-02

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
├── api/                 # Route modules (APIRouter per domain)
├── data/                # External API clients + data pipeline
├── strategy/            # Core PE5Y logic (signal, optimizer, sizer)
├── backtest/            # Cash flow simulation + real data backtest
├── database/            # SQLite helpers (context managers)
└── scheduler/           # APScheduler background jobs
```

### Patterns
- **Configuration**: Frozen dataclasses with env-var defaults (`@dataclass(frozen=True)`)
- **Database access**: Context managers (`connect()` read-only, `connect_rw()` read-write)
- **API clients**: Class-based with rate limiting (`_throttle()` method, `__enter__/__exit__`)
- **Route organization**: One `APIRouter` per domain, prefixed (e.g., `/api/strategy`)
- **Type hints**: Use `from __future__ import annotations`, `Optional`, union types
- **Error handling**: FastAPI `HTTPException` for API errors, `try/except` with logging for background tasks
- **Imports**: Relative imports within package (`from ..config import get_config`)

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
│   ├── portfolio/       # Portfolio page
│   ├── verify/          # Verification page
│   └── data/            # Data management page
└── lib/
    └── api.ts           # Centralized API client + TypeScript interfaces
```

### Patterns
- **Client components**: `"use client"` for interactive pages
- **API client**: Single `api` object with typed methods
- **Styling**: Tailwind CSS 4 with dark mode support (`dark:` variants)
- **State**: React `useState` (no external state management)
- **SSE streaming**: Manual `ReadableStream` reader for price update progress
- **Language**: Vietnamese UI labels (e.g., "Vốn", "Phân tích", "Khuyến nghị")

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
| Files | kebab-case | `market-cap-filter.py` |
| Python modules | snake_case | `signal.py`, `vci_client.py` |
| Python classes | PascalCase | `PE5YCandidate`, `VCIClient` |
| Python functions | snake_case | `generate_signal()`, `select_top_n()` |
| Python constants | UPPER_SNAKE | `CLOSE_SCALE_VND`, `SEED` |
| TypeScript files | kebab-case | `product.controller.ts` |
| TypeScript interfaces | PascalCase | `ConfigResult`, `Position` |
| React components | PascalCase | `Dashboard`, `RootLayout` |
| API routes | kebab-case URLs | `/api/data/update/prices/stream` |
| DB columns | snake_case | `quantity_available`, `created_at` |
| Prisma models | PascalCase | `Product`, `StockTransfer` |

## Commit Standards

- Conventional commit format: `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`
- No AI references in commit messages
- Focused commits (one logical change per commit)
- Never commit `.env`, API keys, or credentials
