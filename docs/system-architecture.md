# System Architecture

> Last updated: 2026-03-03

## High-Level Overview

```
┌─────────────────────────────────────────────────────────┐
│                    PE5Y Fund System                      │
├──────────────┬──────────────────┬───────────────────────┤
│   Frontend   │   PE5Y Backend   │  Inventory Backend    │
│  (Next.js)   │   (FastAPI)      │  (Express/Prisma)     │
│  port:3000   │   port:8002      │  port:3001            │
├──────────────┴──────────────────┴───────────────────────┤
│                                                          │
│  ┌──────────┐   ┌──────────┐   ┌──────────────────────┐ │
│  │ SQLite   │   │PostgreSQL│   │ Cloudflare D1        │ │
│  │ (PE5Y)   │   │(Inventory)│  │ (SEO Automation)     │ │
│  │ local    │   └──────────┘   └──────────────────────┘ │
│  │ gitignore│                                            │
│  └──────────┘                                            │
├─────────────────────────────────────────────────────────┤
│              External Data Sources                        │
│  ┌──────────┐   ┌──────────┐                            │
│  │ VCI API  │   │ KBS API  │                            │
│  │(Vietcap) │   │(KB Sec)  │                            │
│  └──────────┘   └──────────┘                            │
└─────────────────────────────────────────────────────────┘
```

## PE5Y Backend Architecture

### Request Flow

```
Client Request
    │
    ▼
FastAPI App (main.py)
    │
    ├─ CORS middleware (localhost:3000)
    │
    ├─ /api/health
    ├─ /api/data/*      → data_routes.py
    ├─ /api/verify/*    → verify_routes.py
    └─ /api/strategy/*  → strategy_routes.py
```

### Strategy Pipeline

```
optimize(capital, year)
    │
    ▼
signal.py: generate_signal()
    │
    ├─ 1. Query 5-year EPS (all positive, min 3-5 years)
    ├─ 2. Market cap filter (dynamic by year)
    ├─ 3. Liquidity filters (trading days, ADV, zero vol, stale)
    ├─ 4. Compute pe_5y_avg = price / avg_eps_5y
    └─ 5. Rank ascending by pe_5y_avg
    │
    ▼
optimizer.py: optimize()
    │
    ├─ For each select_pct (10%, 12%, 14%, 16%):
    │   ├─ select_top_n(candidates, pct)
    │   ├─ size_portfolio(symbols, capital)
    │   └─ Score: highest CAGR where fill_rate >= 85%
    └─ Return all configs + recommendation
    │
    ├─ benchmark.py: calc_benchmark_cagr()
    │   └─ VNINDEX buy-and-hold CAGR over same period
    │
    ▼
position_sizer.py: size_portfolio()
    │
    ├─ Equal-weight: target_value = capital / N
    ├─ Lot-sized: floor(target_value / price / 100) * 100
    ├─ ADV-constrained: shares_per_day = ADV * 10% participation
    └─ Fill rate: min(1.0, spd * accum_days / target_shares)
```

### Rebalance Calculator Flow

```
Portfolio Page: user inputs cashFlow (B VND)
    │
    ▼
newCapital = currentData.capital_vnd + cashFlow * 1e9
    │
    ▼
api.portfolio(newCapital, pct, year)   ← GET /api/strategy/portfolio
    │
    ▼
computeTrades(currentData.positions, newData.positions)
    │
    ├─ For each symbol in union of old + new:
    │   delta = newShares - oldShares
    │   if delta != 0 → RebalanceTrade { trade_shares: delta }
    │
    ▼
Display: buy orders (delta > 0), sell orders (delta < 0)
         total buy value, total sell value, new cash drag
```

### Data Pipeline

```
Scheduler (6h interval)  ──or──  Manual API trigger
    │                                │
    ▼                                ▼
detect_missing_prices()        POST /api/data/update/prices
    │                                │
    ▼                                ▼
VCIClient.get_ohlcv()         GET /api/data/update/prices/stream [SSE]
    │                          GET /api/data/update/financials/stream [SSE]
    ▼
INSERT OR IGNORE into stock_price_history
```

### Configuration Override Flow

```
config.py: get_config()
    │
    ├─ Load defaults from AppConfig/StrategyConfig dataclasses
    ├─ Read PE5Y_DB_PATH from env (default: ./vietnam_stocks.db)
    └─ Load strategy_config.json (if exists, adjacent to DB)
        └─ Merge overrides into StrategyConfig

PUT /api/strategy/config → save_strategy_config(data)
    │
    └─ Write to strategy_config.json → reload_config()

POST /api/strategy/config/reset
    │
    └─ Delete strategy_config.json → reload_config()
```

### Data Verification Flow

```
GET /api/verify/{symbol}
    │
    ▼
VCIClient.get_annual_ratios()  ──parallel──  KBSClient.get_financial_summary()
    │                                          │
    ▼                                          ▼
VCIFinancialRow                          KBSFinancialRow
    │                                          │
    └────────────┬─────────────────────────────┘
                 ▼
    verifier._compare() per metric
    (eps, pe, pb, roe, revenue, net_profit, bvps)
    Thresholds: 5-10% tolerance
    Status: OK / WARNING / ERROR
```

## Frontend Architecture

```
Next.js App Router
    │
    ├─ / (Dashboard)
    │   └─ Input capital → POST optimize → Display 4 config cards + VNINDEX benchmark
    │
    ├─ /portfolio
    │   ├─ capital + pct params → GET portfolio → Position table (sortable, CSV copy)
    │   ├─ RebalanceCalculator (collapsible)
    │   │   └─ Input cashFlow → GET portfolio(newCapital) → computeTrades() → buy/sell list
    │   └─ ADV Detail (expandable)
    │
    ├─ /config
    │   ├─ GET /api/strategy/config → display all StrategyConfig fields
    │   ├─ Edit fields → PUT /api/strategy/config → save
    │   └─ Reset → POST /api/strategy/config/reset
    │
    ├─ /verify
    │   └─ Input symbol → GET verify/{symbol} → Comparison table
    │
    └─ /data
        ├─ GET /api/data/health → DB coverage report
        ├─ GET /api/data/update/prices/stream → SSE progress
        └─ GET /api/data/update/financials/stream → SSE progress
```

### API Client Pattern

```typescript
// Centralized in frontend/src/lib/api.ts
export const api = {
  optimize: (capital, year?) => fetchJson("/api/strategy/optimize", { capital }),
  portfolio: (capital, pct, year?) => fetchJson("/api/strategy/portfolio", { capital, pct }),
  verify: (symbol, year?) => fetchJson(`/api/verify/${symbol}`),
  streamPriceUpdate: (onEvent, onDone) => _streamSSE(url, onEvent, onDone),
  streamFinancialsUpdate: (onEvent, onDone, year?) => _streamSSE(url, onEvent, onDone),
  strategyConfig: () => fetchJson("/api/strategy/config"),
  saveStrategyConfig: (data) => putJson("/api/strategy/config", data),
  resetStrategyConfig: () => postJson("/api/strategy/config/reset"),
};

// Shared SSE helper — returns AbortController for cancellation
function _streamSSE(url, onEvent, onDone): AbortController
```

### Shared Formatter Pattern

```typescript
// frontend/src/lib/format.ts — imported by portfolio/page.tsx, rebalance-calculator.tsx
export function fmtVND(v: number): string    // e.g. 1.2T, 3.5B, 200M
export function fmtPrice(v: number): string  // e.g. 25.3k (stored_vnd / 1000)
export function fillColor(rate: number): string  // Tailwind text-* class by threshold
```

## Inventory Backend Architecture

```
Express App
    │
    ├─ /health
    ├─ /api/products    → ProductController → ProductService → ProductRepository → Prisma
    └─ /api/warehouses  → WarehouseController → ...
```

### Database Models

```
User (UUID, email, password, role)
    │
Warehouse (UUID, name, locationCode, address)
    │
    ├── Inventory (productId, warehouseId, quantityAvailable/Reserved/Incoming)
    ├── InventoryMovement (movementType, quantity, quantityBefore/After)
    ├── StockTransfer (fromWarehouseId, toWarehouseId, status)
    └── BatchLot (batchNumber, quantity, expiryDate)
    │
Product (UUID, sku, name, costPriceUsd, sellingPriceUsd)
```

## SEO Automation Architecture

```
Cloudflare Worker (Hono)
    │
    ├─ Cron trigger (daily 00:00 UTC)
    ├─ POST /api/trigger-scan → Durable Object (SiteCrawler)
    │
    ▼
SiteCrawler DO
    │
    ├─ Hybrid crawler (simple fetch → CF Browser fallback)
    ├─ Analyzers (meta tags, headers, accessibility)
    ├─ AI rewrite engine (Claude API)
    └─ D1 database (scan results, issues)
```

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| PE5Y DB | SQLite, local, gitignored | Single-file portability; 1.6GB excluded from git; path via env var |
| PE5Y backend | FastAPI | Async support, auto-docs, Python ecosystem for finance |
| No ORM for PE5Y | Raw SQL | Performance-critical aggregate queries, complex GROUP BY/HAVING |
| Inventory DB | PostgreSQL + Prisma | Relational integrity, migrations, type-safe ORM |
| Frontend | Next.js 16 | React 19, App Router, Tailwind CSS integration |
| Config | Frozen dataclasses + JSON overrides | Immutable defaults + runtime mutability without restart |
| Rate limiting | Per-client throttle | Respect broker API limits (30 RPM each) |
| Data updates | SSE streaming | Real-time progress for long-running batch operations |
| Market cap filter | Dynamic by year | Accounts for VN market growth (10% per 2 years from 2015) |
| Rebalance Calculator | Client-side diff of two `/portfolio` calls | No new backend endpoint; reuses existing position sizer |
| Shared formatters | `lib/format.ts` | DRY — avoids duplicating VND/price/color logic across components |
| Benchmark | VNINDEX buy-and-hold CAGR | Strategy vs passive index comparison in optimize response |

## Ports & Services

| Service | Port | Protocol |
|---------|------|----------|
| PE5Y Backend (FastAPI) | 8002 | HTTP |
| Inventory Backend (Express) | 3001 | HTTP |
| Frontend (Next.js) | 3000 | HTTP |
| SEO Worker | - | Cloudflare Workers |
