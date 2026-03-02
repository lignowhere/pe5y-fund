# Codebase Summary

> Last updated: 2026-03-02

## Repository Structure

```
pe5y-fund/
├── CLAUDE.md                     # AI agent instructions
├── .env                          # PE5Y_DB_PATH config
├── requirements.txt              # Python deps (FastAPI, httpx, APScheduler)
├── vietnam_stocks.db             # SQLite DB (prices + financials)
├── backend/                      # Dual backend: Python PE5Y + Node.js Inventory
│   ├── main.py                   # FastAPI entry point (PE5Y)
│   ├── config.py                 # Central configuration (dataclasses)
│   ├── api/                      # FastAPI route modules
│   │   ├── data_routes.py        # Data status, updates, search, SSE stream
│   │   ├── strategy_routes.py    # Optimizer, portfolio, sensitivity
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
│   │   └── optimizer.py          # Multi-config comparison + recommendation
│   ├── backtest/                 # Cash flow backtesting
│   │   ├── cashflow_sim.py       # Synthetic data simulation
│   │   └── cashflow_real.py      # Real data backtest
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
├── frontend/                     # Next.js 16 React 19 frontend
│   ├── package.json              # Next.js, React 19, Tailwind CSS 4
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx        # Root layout with nav (Dashboard/Portfolio/Verify/Data)
│   │   │   ├── page.tsx          # Strategy optimizer dashboard
│   │   │   ├── portfolio/page.tsx# Portfolio position viewer
│   │   │   ├── verify/page.tsx   # Data verification UI
│   │   │   └── data/page.tsx     # Data management + streaming updates
│   │   └── lib/
│   │       └── api.ts            # API client with TypeScript interfaces
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
| File | Purpose | Lines |
|------|---------|-------|
| `backend/strategy/signal.py` | PE5Y signal generation, EPS/market cap/liquidity filters | ~263 |
| `backend/strategy/optimizer.py` | Compare 10/12/14/16% configs, recommend best | ~130 |
| `backend/strategy/position_sizer.py` | ADV-aware equal-weight sizing | ~140 |
| `backend/strategy/market_cap_filter.py` | Dynamic floor: 200B * (1.1)^periods | ~19 |

### Data Pipeline
| File | Purpose | Lines |
|------|---------|-------|
| `backend/data/vci_client.py` | VCI GraphQL API (financial ratios, OHLCV) | ~202 |
| `backend/data/kbs_client.py` | KBS REST API (profile, financials) | ~135 |
| `backend/data/updater.py` | Missing data detection, update orchestrator, SSE stream | ~378 |
| `backend/data/verifier.py` | VCI vs KBS cross-validation | ~163 |

### API Layer
| File | Purpose | Lines |
|------|---------|-------|
| `backend/api/strategy_routes.py` | `/api/strategy/optimize`, `/portfolio`, `/history` | ~118 |
| `backend/api/data_routes.py` | `/api/data/status`, `/update/prices/stream`, `/search` | ~113 |
| `backend/api/verify_routes.py` | `/api/verify/{symbol}`, `/batch/check` | ~67 |

### Frontend
| File | Purpose | Lines |
|------|---------|-------|
| `frontend/src/app/page.tsx` | Strategy optimizer dashboard UI | ~121 |
| `frontend/src/lib/api.ts` | Full API client with types + SSE streaming | ~228 |

## Database Schema (SQLite — vietnam_stocks.db)

Tables (inferred from queries):
- `stocks` — `ticker`, `organ_name`
- `stock_exchange` — `ticker`, `exchange` (HSX/HNX/UPCOM)
- `stock_price_history` — `symbol`, `time`, `open`, `high`, `low`, `close`, `volume`
- `financial_ratios` — `symbol`, `year`, `quarter`, `eps_vnd`, `market_cap_billions`, etc.

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
