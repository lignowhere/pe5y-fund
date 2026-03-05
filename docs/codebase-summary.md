# Codebase Summary

> Last updated: 2026-03-04

## Repository Structure

```
pe5y-fund/
├── CLAUDE.md                     # AI agent instructions
├── README.md                     # Project overview
├── .env                          # PE5Y_DB_PATH config (gitignored)
├── .gitignore                    # Excludes *.db, .env, __pycache__, node_modules, strategy_config.json
├── requirements.txt              # Python deps (FastAPI, httpx, APScheduler)
├── vietnam_stocks.db             # SQLite DB (gitignored — local copy)
├── backend/                      # Dual backend: Python Fund + Node.js Inventory
│   ├── main.py                   # FastAPI entry point (v0.2.0, title "PE_TTM_20Q Fund System")
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
│   ├── strategy/                 # Core strategy logic
│   │   ├── signal_pe_ttm_20q.py  # PE_TTM_20Q signal generator (PRODUCTION)
│   │   ├── signal.py             # PE5Y signal generator (reference/comparison only)
│   │   ├── market_cap_filter.py  # Dynamic market cap floor by year
│   │   ├── position_sizer.py     # ADV-aware portfolio sizing
│   │   ├── optimizer.py          # Multi-config comparison + recommendation
│   │   ├── benchmark.py          # VNINDEX buy-and-hold CAGR comparison
│   │   └── ktpl_adjustment.py    # KTPL welfare fund leakage estimation (tested, rejected)
│   ├── backtest/                 # Cash flow backtesting
│   │   ├── cashflow_sim.py       # Synthetic data simulation
│   │   ├── cashflow_real.py      # Real data backtest (uses ./vietnam_stocks.db)
│   │   ├── sensitivity_runner.py # Multi-strategy backtest engine (12mo x 4pcts x 3strats)
│   │   ├── capital_deployment_sim.py # DCA deployment comparison (add_existing vs fresh_signal)
│   │   ├── run_comparison.py     # CLI: runs sensitivity + saves optimizer JSON
│   │   └── run_deployment.py     # CLI: runs capital deployment comparison
│   ├── report/                   # Report generation
│   │   └── pdf_report.py         # Strategy comparison PDF report
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
│   │   │   ├── layout.tsx        # Root layout with nav — title "PE_TTM_20Q Strategy Optimizer"
│   │   │   ├── page.tsx          # Strategy optimizer dashboard
│   │   │   ├── portfolio/
│   │   │   │   ├── page.tsx      # Portfolio position viewer + CSV copy (pe_ratio column)
│   │   │   │   └── rebalance-calculator.tsx  # Deposit/withdraw → buy/sell orders
│   │   │   ├── config/page.tsx   # Live strategy parameter editor
│   │   │   ├── verify/page.tsx   # Data verification UI
│   │   │   └── data/
│   │   │       ├── page.tsx      # Data management + streaming updates
│   │   │       └── update-progress-panel.tsx  # SSE progress panel component
│   │   └── lib/
│   │       ├── api.ts            # API client with TypeScript interfaces (pe_ratio field)
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
| `backend/strategy/signal_pe_ttm_20q.py` | PE_TTM_20Q signal: 20-quarter trailing EPS, look-ahead bias prevention via `_LATEST_Q_BY_MONTH` |
| `backend/strategy/signal.py` | PE5Y signal (reference only — 5-year annual EPS) |
| `backend/strategy/optimizer.py` | Compare 10/12/14/16% configs via `generate_signal_20q`, recommend best |
| `backend/strategy/position_sizer.py` | ADV-aware equal-weight sizing; field renamed `pe_ratio` |
| `backend/strategy/market_cap_filter.py` | Dynamic floor: 200B * (1.1)^periods from 2015 |
| `backend/strategy/benchmark.py` | VNINDEX buy-and-hold CAGR over backtest period |
| `backend/strategy/ktpl_adjustment.py` | Welfare fund leakage estimation (tested, rejected) |

### Backtest Engine
| File | Purpose |
|------|---------|
| `backend/backtest/cashflow_real.py` | Real data backtest using PE_TTM_20Q_RELAXED signal |
| `backend/backtest/sensitivity_runner.py` | 72-run sweep: 12 months x 4 pcts x 3 strategies; `save_for_optimizer()` |
| `backend/backtest/capital_deployment_sim.py` | DCA deployment: add_existing vs fresh_signal comparison |
| `backend/backtest/run_comparison.py` | CLI runner: sensitivity sweep → saves optimizer JSON |
| `backend/backtest/run_deployment.py` | CLI runner: capital deployment comparison |
| `backend/report/pdf_report.py` | Strategy comparison PDF report generator |

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
| `backend/api/strategy_routes.py` | `/api/strategy/optimize`, `/portfolio`, `/history`, `/config`; uses PE_TTM_20Q signal |
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
| `frontend/src/app/page.tsx` | Strategy optimizer dashboard ("PE_TTM_20Q Strategy Optimizer") |
| `frontend/src/app/portfolio/page.tsx` | Position table with `pe_ratio` column, CSV copy, sort, RebalanceCalculator |
| `frontend/src/app/portfolio/rebalance-calculator.tsx` | Deposit/withdraw → buy/sell order diff |
| `frontend/src/app/config/page.tsx` | Live strategy config editor (all StrategyConfig fields) |
| `frontend/src/lib/api.ts` | Full API client with TypeScript interfaces + SSE streaming |
| `frontend/src/lib/format.ts` | Shared: `fmtVND`, `fmtPrice`, `fillColor` |

## Key TypeScript Interfaces (frontend/src/lib/api.ts)

```typescript
ConfigResult        // select_pct, stock_count, avg_fill_rate, historical_cagr, recommended
OptimizeResponse    // results: ConfigResult[], benchmark: BenchmarkData
Position            // symbol, signal_rank, pe_ratio, current_price_vnd, fill_rate, ...
PortfolioResult     // formation_year, select_pct, capital_vnd, summary, positions
RebalanceTrade      // symbol, old_shares, new_shares, trade_shares, trade_value_vnd
StrategyConfig      // all configurable fields matching backend StrategyConfig dataclass
StreamProgress      // SSE event: type, symbol, index, total, updated, failed, inserted
```

Note: `pe_ratio` replaced `pe_5y_avg` in all frontend interfaces and API responses.

## Database Schema (SQLite — vietnam_stocks.db)

Tables:
- `stocks` — `ticker`, `organ_name`
- `stock_exchange` — `ticker`, `exchange` (HSX/HNX/UPCOM)
- `stock_price_history` — `symbol`, `time`, `open`, `high`, `low`, `close`, `volume`
  - `close` stored in thousands of VND (`close * 1000 = price_vnd`)
- `financial_ratios` — `symbol`, `year`, `quarter`, `eps_vnd`, `market_cap_billions`, etc.
  - Annual EPS: `quarter=NULL`
  - Quarterly EPS: `quarter=1-4` (used by PE_TTM_20Q signal)
  - `market_cap_billions` is actually in VND units (misleading column name)

## Database Schema (PostgreSQL — Inventory)

Models: `User`, `Warehouse`, `Product`, `Inventory`, `InventoryMovement`, `StockTransfer`, `BatchLot`

## Tech Stack Summary

| Layer | Technology | Version |
|-------|-----------|---------|
| Fund Backend | Python, FastAPI, httpx, APScheduler | Python 3.12+ |
| Fund Database | SQLite | — |
| Inventory Backend | Node.js, Express 5, Prisma, Zod | Node 20+ |
| Inventory Database | PostgreSQL | — |
| Frontend | Next.js 16, React 19, Tailwind CSS 4 | — |
| SEO Worker | Cloudflare Workers, Hono, D1 | — |
| SEO Dashboard | Vite, React | — |

## Recent Changes (2026-03-04) — v0.2.0 Migration

- **Production signal migrated**: PE5Y → PE_TTM_20Q_RELAXED (31.74% CAGR, 88% win rate)
- **New signal file**: `backend/strategy/signal_pe_ttm_20q.py` — `generate_signal_20q()`, `PE20QCandidate` dataclass, `_LATEST_Q_BY_MONTH` look-ahead bias guard
- **PE5Y retained**: `backend/strategy/signal.py` kept for reference/comparison backtests
- **Field rename**: `pe_5y_avg` → `pe_ratio` in `strategy_routes.py`, `position_sizer.py`, `frontend/src/lib/api.ts`, `frontend/src/app/portfolio/page.tsx`
- **Sensitivity runner**: `backend/backtest/sensitivity_runner.py` — 72-run sweep across 3 strategies, `save_for_optimizer()` saves JSON for optimizer heatmap
- **Capital deployment sim**: `backend/backtest/capital_deployment_sim.py` — add_existing vs fresh_signal negligible (<0.4pp)
- **KTPL adjustment**: `backend/strategy/ktpl_adjustment.py` — welfare fund leakage estimation, tested and rejected (too noisy, -0.5pp)
- **PDF report**: `backend/report/pdf_report.py` — strategy comparison report scaffolded
- **CLI runners**: `backend/backtest/run_comparison.py`, `run_deployment.py`
- **Version bump**: `backend/main.py` → version 0.2.0, title "PE_TTM_20Q Fund System"
- **Frontend titles**: `layout.tsx` → "PE_TTM_20Q Strategy Optimizer"; `config/page.tsx` heading updated
