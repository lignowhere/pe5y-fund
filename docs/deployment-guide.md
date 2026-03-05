# Deployment Guide

> Last updated: 2026-03-04

## Prerequisites

- Python 3.12+
- Node.js 20+
- SQLite database file (`vietnam_stocks.db`) — obtain separately (not in git)

## Fund Backend (PE_TTM_20Q)

### Environment Setup

```bash
# At project root
echo "PE5Y_DB_PATH=./vietnam_stocks.db" > .env
```

The `PE5Y_DB_PATH` env var controls where the backend looks for the SQLite database.
Default is `./vietnam_stocks.db` (relative to where `uvicorn` is invoked).

### Install & Run

```bash
pip install -r requirements.txt

# From project root (so relative DB path resolves correctly)
uvicorn backend.main:app --host 127.0.0.1 --port 8002 --reload
```

App starts as "PE_TTM_20Q Fund System" v0.2.0.

### Config Overrides

Strategy parameters can be tuned at runtime without restart:
- Edit via frontend `/config` page, or
- Manually write `strategy_config.json` adjacent to the DB file

`strategy_config.json` is gitignored and takes effect on the next API call.

### Health Check

```
GET http://127.0.0.1:8002/api/health
```

## Frontend

```bash
cd frontend
npm install

# Development
npm run dev     # http://localhost:3000

# Production build
npm run build
npm start
```

### Environment Variables

```bash
# frontend/.env.local
NEXT_PUBLIC_API_URL=http://127.0.0.1:8002
```

Default is `http://127.0.0.1:8002` if not set.

## Inventory Backend

```bash
cd backend
npm install
npx prisma generate
npx prisma migrate dev    # creates PostgreSQL schema
npm run dev               # http://localhost:3001
```

Requires a PostgreSQL instance. Set `DATABASE_URL` in `backend/.env`.

## Database Notes

- `vietnam_stocks.db` is **not tracked in git** (gitignored via `*.db`)
- The DB is ~1.6GB when fully populated
- Obtain the initial DB from a team member or rebuild via data update endpoints:
  ```
  GET /api/data/update/prices/stream
  GET /api/data/update/financials/stream
  ```
- The background scheduler auto-updates every 6 hours when the backend is running
- `financial_ratios` table must contain quarterly rows (`quarter=1-4`) for PE_TTM_20Q signal to function

## Sensitivity JSON (Optional)

For historical CAGR heatmap in the optimizer, place `sensitivity-pe5y-results.json` at:
- `./sensitivity-pe5y-results.json` (project root), or
- `./output/sensitivity-pe5y-results.json`

The optimizer searches both locations via `_load_sensitivity_data(db_path.parent)`.

To regenerate from scratch:

```bash
# From project root
python -m backend.backtest.run_comparison
# Writes output/sensitivity-pe5y-results.json
```

## Running the Backtest

```bash
# Real data backtest using PE_TTM_20Q_RELAXED (from project root)
python -m backend.backtest.cashflow_real

# Multi-strategy sensitivity sweep (12 months x 4 pcts x 3 strategies)
python -m backend.backtest.run_comparison

# Capital deployment comparison (add_existing vs fresh_signal)
python -m backend.backtest.run_deployment
```

## SEO Automation

```bash
cd seo-automation/worker
npm install
npx wrangler dev    # local development
npx wrangler deploy # production deploy to Cloudflare
```

Requires Cloudflare account with D1 and Workers configured in `wrangler.toml`.
