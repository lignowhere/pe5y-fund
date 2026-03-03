# Codebase Summary

> Last updated: 2026-03-03

## Repository Structure

```
pe5y-fund/
├── CLAUDE.md                     # AI agent instructions
├── .env                          # PE5Y_DB_PATH config (gitignored)
├── .gitignore                    # Excludes *.db, .env, __pycache__, node_modules, strategy_config.json
├── requirements.txt              # Python deps (FastAPI, httpx, APScheduler)
├── vietnam_stocks.db             # SQLite DB (gitignored — local copy)
├── backend/                      # Dual backend: Python PE5Y + Node.js Inventory
│   ├── main.py                   # FastAPI entry point (PE5Y)
│   ├── config.py                 # Central configuration (frozen dataclasses + JSON overrides)
│   ├── api/                      # FastAPI route modules
│   │   ├── data_routes.py        # Data status, updates, search, SSE streams
│   │   ├── strategy_routes.py    # Optimizer, portfolio, sensitivity, config CRUD
│   │   └── verify_routes.py      # VCI vs KBS cross-check
│   ├── data/                     # Data clients and pipeline
│   │   ├── vci_client.py         # Vietcap GraphQL/REST client
│   │   ├── kbs_client.py         # KB Securities REST client
│   │   ├── updater.py            # Missing data detection + update orchestrator
│   │   └── verifier.py           # Cross-source verification logic
│   ├── strategy/                 # Core PE5Y logic
│   │   ├── signal.py             # Signal generation (EPS, market cap, liquidity)
│   │   ├── market_cap_filter.py  # Dynamic market cap floor by year
│   │   ├── position_sizer.py     # ADV-aware portfolio sizing
│   │   ├── optimizer.py          # Multi-config comparison + recommendation
│   │   └── benchmark.py          # VNINDEX buy-and-hold CAGR comparison
│   ├── backtest/                 # Cash flow backtesting
│   │   ├── cashflow_sim.py       # Synthetic data simulation
│   │   └── cashflow_real.py      # Real data backtest (uses ./vietnam_stocks.db)
│   ├── database/                 # SQLite connection helpers
│   │   └── connection.py         # Context managers, fetch helpers
│   ├── scheduler/                # Background job scheduling
│   │   └── __init__.py           # APScheduler 6h interval auto-update
│   ├── src/                      # Express/TypeScript inventory backend
│   │   ├── index.ts              # Express entry point
│   │   ├── config/               # Prisma client config
│   │   ├── products/             # Product CRUD (controller/service/repo/routes/schemas)
│   │   ├── warehouses/           # Warehouse CRUD
│   │   └── middleware/           # Error handler, async handler, validation
│   ├── prisma/                   # Prisma schema + migrations
│   │   ├── schema.prisma         # User, Warehouse, Product, Inventory, etc.
│   │   └── seed.ts               # Database seeder
│   └── package.json              # Node.js deps (Express, Prisma, Zod)
├── frontend/                     # Next.js 16 React 19 frontend (git submodule)
│   ├── package.json              # Next.js, React 19, Tailwind CSS 4
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx        # Root layout with nav (Dashboard/Portfolio/Config/Verify/Data)
│   │   │   ├── page.tsx          # Strategy optimizer dashboard
│   │   │   ├── portfolio/
│   │   │   │   ├── page.tsx      # Portfolio position viewer + CSV copy
│   │   │   │   └── rebalance-calculator.tsx  # Deposit/withdraw → buy/sell orders
│   │   │   ├── config/page.tsx   # Live strategy parameter editor
│   │   │   ├── verify/page.tsx   # Data verification UI
│   │   │   └── data/
│   │   │       ├── page.tsx      # Data management + streaming updates
│   │   │       └── update-progress-panel.tsx  # SSE progress panel component
│   │   └── lib/
│   │       ├── api.ts            # API client with TypeScript interfaces
│   │       └── format.ts         # Shared formatters: fmtVND, fmtPrice, fillColor
├── seo-automation/               # Cloudflare Workers SEO scanner
│   ├── worker/                   # Hono-based Worker
│   │   ├── wrangler.toml         # CF config (D1, Durable Objects, Cron)
│   │   └── src/
│   │       ├── index.ts          # Main worker entry
│   │       ├── analyzers/        # Meta tag, header, accessibility analyzers
│   │       ├── clients/          # Crawlers (fetch, CF Browser, hybrid)
│   │       ├── services/         # AI rewrite engine, SEO analyzer, DB service
│   │       └── durable-objects/  # SiteCrawler Durable Object
│   ├── dashboard/                # Vite + React dashboard
│   └── shared/types/             # Shared TypeScript types
└── agent/                        # Claude Code agent config (skills, workflows)
```

## Key Files by Function

### Strategy Engine
| File | Purpose |
|------|---------|
| `backend/strategy/signal.py` | PE5Y signal generation, EPS/market cap/liquidity filters |
| `backend/strategy/optimizer.py` | Compare 10/12/14/16% configs, recommend best |
| `backend/strategy/position_sizer.py` | ADV-aware equal-weight sizing |
| `backend/strategy/market_cap_filter.py` | Dynamic floor: 200B * (1.1)^periods |
| `backend/strategy/benchmark.py` | VNINDEX buy-and-hold CAGR over backtest period |

### Data Pipeline
| File | Purpose |
|------|---------|
| `backend/data/vci_client.py` | VCI GraphQL API (financial ratios, OHLCV) |
| `backend/data/kbs_client.py` | KBS REST API (profile, financials) |
| `backend/data/updater.py` | Missing data detection, update orchestrator, SSE stream |
| `backend/data/verifier.py` | VCI vs KBS cross-validation |

### API Layer
| File | Purpose |
|------|---------|
| `backend/api/strategy_routes.py` | `/api/strategy/optimize`, `/portfolio`, `/history`, `/config` |
| `backend/api/data_routes.py` | `/api/data/status`, `/update/prices/stream`, `/update/financials/stream`, `/search` |
| `backend/api/verify_routes.py` | `/api/verify/{symbol}`, `/batch/check` |

### Configuration
| File | Purpose |
|------|---------|
| `backend/config.py` | Frozen dataclasses, env-var defaults, JSON override support |
| `.env` | `PE5Y_DB_PATH=./vietnam_stocks.db` |
| `strategy_config.json` | Runtime overrides (gitignored, adjacent to DB) |

### Frontend
| File | Purpose |
|------|---------|
| `frontend/src/app/page.tsx` | Strategy optimizer dashboard (VNINDEX benchmark display) |
| `frontend/src/app/portfolio/page.tsx` | Position table, CSV copy, sort, RebalanceCalculator |
| `frontend/src/app/portfolio/rebalance-calculator.tsx` | Deposit/withdraw → buy/sell order diff |
| `frontend/src/app/config/page.tsx` | Live strategy config editor (all StrategyConfig fields) |
| `frontend/src/lib/api.ts` | Full API client with TypeScript interfaces + SSE streaming |
| `frontend/src/lib/format.ts` | Shared: `fmtVND`, `fmtPrice`, `fillColor` |

## Key TypeScript Interfaces (frontend/src/lib/api.ts)

```typescript
ConfigResult        // select_pct, stock_count, avg_fill_rate, historical_cagr, recommended
OptimizeResponse    // results: ConfigResult[], benchmark: BenchmarkData
Position            // symbol, signal_rank, pe_5y_avg, current_price_vnd, fill_rate, ...
PortfolioResult     // formation_year, select_pct, capital_vnd, summary, positions
RebalanceTrade      // symbol, old_shares, new_shares, trade_shares, trade_value_vnd
StrategyConfig      // all configurable fields matching backend StrategyConfig dataclass
StreamProgress      // SSE event: type, symbol, index, total, updated, failed, inserted
```

## Database Schema (SQLite — vietnam_stocks.db)

Tables (inferred from queries):
- `stocks` — `ticker`, `organ_name`
- `stock_exchange` — `ticker`, `exchange` (HSX/HNX/UPCOM)
- `stock_price_history` — `symbol`, `time`, `open`, `high`, `low`, `close`, `volume`
  - `close` stored in thousands of VND (`close * 1000 = price_vnd`)
- `financial_ratios` — `symbol`, `year`, `quarter`, `eps_vnd`, `market_cap_billions`, etc.
  - `market_cap_billions` is actually in VND units (misleading column name)

## Database Schema (PostgreSQL — Inventory)

Models: `User`, `Warehouse`, `Product`, `Inventory`, `InventoryMovement`, `StockTransfer`, `BatchLot`

## Tech Stack Summary

| Layer | Technology | Version |
|-------|-----------|---------|
| PE5Y Backend | Python, FastAPI, httpx, APScheduler | Python 3.12+ |
| PE5Y Database | SQLite | - |
| Inventory Backend | Node.js, Express 5, Prisma, Zod | Node 20+ |
| Inventory Database | PostgreSQL | - |
| Frontend | Next.js 16, React 19, Tailwind CSS 4 | - |
| SEO Worker | Cloudflare Workers, Hono, D1 | - |
| SEO Dashboard | Vite, React | - |

## Recent Changes (2026-03-03)

- **Rebalance Calculator**: New component at `frontend/src/app/portfolio/rebalance-calculator.tsx` — input deposit/withdraw amount, compute buy/sell order diff between old and new capital positions
- **DB path localized**: `vietnam_stocks.db` moved from external path to local `./vietnam_stocks.db`; root `.gitignore` added; 1.6GB DB removed from git tracking
- **Hardcoded path cleanup**: `cashflow_real.py` uses `os.environ.setdefault("PE5Y_DB_PATH", "./vietnam_stocks.db")`; `optimizer.py` resolves sensitivity JSON relative to `db_path.parent`
- **Shared formatters**: `fmtVND`, `fmtPrice`, `fillColor` extracted to `frontend/src/lib/format.ts`; consumed by `portfolio/page.tsx` and `rebalance-calculator.tsx`
- **Frontend submodule**: All app pages committed (dashboard, portfolio, config, data, verify)
- **VNINDEX benchmark**: `backend/strategy/benchmark.py` added; `optimize` endpoint returns `benchmark` field
- **Config API**: Full CRUD via `GET/PUT /api/strategy/config`, `POST /api/strategy/config/reset`, `GET /api/strategy/config/defaults`
