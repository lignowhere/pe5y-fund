# PE_TTM_20Q Fund System

Quantitative investment tool for Vietnam stock market using the PE_TTM_20Q_RELAXED strategy.

## What is PE_TTM_20Q?

Select stocks with the lowest P/E ratio based on 20-quarter trailing average EPS. The RELAXED variant only requires avg EPS > 0 (not all 20 quarters positive), yielding a larger universe (21-26 stocks vs 11-12 for strict). Applies market cap, liquidity, and data quality filters, then sizes positions with ADV (Average Daily Volume) constraints. Rebalances annually on September 1.

**Backtested CAGR**: 31.74% (PE_TTM_20Q_RELAXED, Sep-1, 14% select, 2015-2025) vs VNINDEX 8.66%

> PE5Y (5-year annual EPS) is retained in `backend/strategy/signal.py` for reference and comparison backtests.

## Architecture

```
┌──────────────┬──────────────────┬───────────────────────┐
│   Frontend   │  Fund Backend    │  Inventory Backend    │
│  (Next.js)   │   (FastAPI)      │  (Express/Prisma)     │
│  :3000       │   :8002          │  :3001                │
└──────────────┴──────────────────┴───────────────────────┘
       │               │                    │
       │          ┌────┴────┐          ┌────┴────┐
       │          │ SQLite  │          │PostgreSQL│
       │          └─────────┘          └─────────┘
       │               │
       │         ┌─────┴──────┐
       │         │ VCI + KBS  │  (Vietnamese broker APIs)
       │         └────────────┘
```

## Sub-Projects

| Project | Stack | Purpose |
|---------|-------|---------|
| **Fund Backend** | Python, FastAPI, SQLite | Strategy engine, data pipeline, backtesting |
| **Frontend** | Next.js 16, React 19, Tailwind | Dashboard, portfolio viewer, config, data management |
| **Inventory Backend** | Express 5, Prisma, PostgreSQL | Multi-channel inventory management |
| **SEO Automation** | Cloudflare Workers, Hono, D1 | Automated SEO scanning + AI rewrite |

## Quick Start

### Fund Backend (Python)

```bash
# Install dependencies
pip install -r requirements.txt

# Set database path (local SQLite — gitignored)
echo "PE5Y_DB_PATH=./vietnam_stocks.db" > .env

# Run server
uvicorn backend.main:app --host 127.0.0.1 --port 8002 --reload
```

### Frontend (Next.js)

```bash
cd frontend
npm install
npm run dev   # http://localhost:3000
```

### Inventory Backend (Node.js)

```bash
cd backend
npm install
npx prisma generate
npx prisma migrate dev
npm run dev   # http://localhost:3001
```

## API Endpoints

### Strategy

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/strategy/optimize?capital=10000000000` | Compare 10/12/14/16% configs, recommend best + VNINDEX benchmark |
| GET | `/api/strategy/portfolio?capital=10B&pct=14` | Full portfolio with position sizing |
| GET | `/api/strategy/history/sensitivity` | 72-run sensitivity heatmap data |
| GET | `/api/strategy/config` | Get current strategy config |
| PUT | `/api/strategy/config` | Save strategy config overrides |
| POST | `/api/strategy/config/reset` | Reset config to defaults |

### Data Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/data/status` | Data freshness summary |
| GET | `/api/data/health` | Comprehensive DB coverage report |
| GET | `/api/data/missing/prices` | Symbols with stale price data |
| POST | `/api/data/update/prices` | Trigger price update |
| GET | `/api/data/update/prices/stream` | SSE streaming price update |
| GET | `/api/data/update/financials/stream` | SSE streaming financials update |
| GET | `/api/data/search?q=VNM` | Search symbols by ticker/name |

### Verification

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/verify/{symbol}` | Cross-check VCI vs KBS data |
| GET | `/api/verify/batch/check?symbols=VNM,FPT` | Batch verification (max 50) |

## Strategy Pipeline

1. **Signal generation** — Query 20 quarters of quarterly EPS, apply market cap floor (200B VND base, +10%/2yr from 2015), apply liquidity filters (trading days, ADV, zero volume, stale close)
2. **Ranking** — Compute `pe_ratio = price_vnd / avg_quarterly_eps[20q]`, rank ascending
3. **Selection** — Top N% of universe (configurable: 10/12/14/16%)
4. **Position sizing** — Equal-weight with ADV constraints (10% participation, 100-share lots)
5. **Optimization** — Recommend config with highest historical CAGR where fill_rate >= 85%

## Backtest Summary

| Strategy | CAGR | Universe size | Notes |
|----------|------|---------------|-------|
| PE_TTM_20Q_RELAXED | **31.74%** | 21-26 stocks | **Production** |
| PE5Y | 29.82% | 11-12 stocks | Reference only |
| VNINDEX | 8.66% | — | Benchmark |
| KTPL-adjusted | ~31.2% | 21-26 stocks | Tested, rejected (noisy, -0.5pp) |

## Data Sources

- **VCI (Vietcap)** — Financial ratios via GraphQL, OHLCV via REST (30 RPM)
- **KBS (KB Securities)** — Financial summary, stock profiles (30 RPM)
- **Cross-validation** — VCI vs KBS comparison with configurable tolerance thresholds

## Database

### SQLite (Fund) — `./vietnam_stocks.db`
- Stored locally, gitignored (not committed to repository)
- Configure path via `PE5Y_DB_PATH` env var (defaults to `./vietnam_stocks.db`)
- `stocks` — ticker, company name
- `stock_exchange` — ticker to exchange mapping (HSX/HNX/UPCOM)
- `stock_price_history` — daily OHLCV (close stored in thousands of VND)
- `financial_ratios` — annual/quarterly EPS, P/E, P/B, ROE, market cap (annual: `quarter=NULL`; quarterly: `quarter=1-4`)

### PostgreSQL (Inventory)
- User, Warehouse, Product, Inventory, InventoryMovement, StockTransfer, BatchLot

## Frontend Pages

- **Dashboard** (`/`) — Input capital, compare strategy configs, get recommendation with VNINDEX benchmark
- **Portfolio** (`/portfolio`) — Position table with fill rates, ADV data, and Rebalance Calculator (deposit/withdraw)
- **Config** (`/config`) — Live strategy parameter editor (filters, sizing, costs)
- **Verify** (`/verify`) — VCI vs KBS data cross-check for any symbol
- **Data** (`/data`) — DB health report, missing data detection, streaming updates

## Documentation

Detailed docs in [`./docs/`](./docs/):
- [Project Overview & PDR](./docs/project-overview-pdr.md)
- [Codebase Summary](./docs/codebase-summary.md)
- [Code Standards](./docs/code-standards.md)
- [System Architecture](./docs/system-architecture.md)
- [Project Roadmap](./docs/project-roadmap.md)
- [Deployment Guide](./docs/deployment-guide.md)

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Fund Backend | Python 3.12+, FastAPI, httpx, APScheduler, SQLite |
| Inventory Backend | Node.js, Express 5, Prisma, PostgreSQL, Zod |
| Frontend | Next.js 16, React 19, Tailwind CSS 4, TypeScript |
| SEO Automation | Cloudflare Workers, Hono, D1, Durable Objects |
