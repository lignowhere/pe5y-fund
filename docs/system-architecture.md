# System Architecture

> Last updated: 2026-03-02

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
│  └──────────┘   └──────────┘   └──────────────────────┘ │
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
    ▼
position_sizer.py: size_portfolio()
    │
    ├─ Equal-weight: target_value = capital / N
    ├─ Lot-sized: floor(target_value / price / 100) * 100
    ├─ ADV-constrained: shares_per_day = ADV * 10% participation
    └─ Fill rate: min(1.0, spd * accum_days / target_shares)
```

### Data Pipeline

```
Scheduler (6h interval)  ──or──  Manual API trigger
    │                                │
    ▼                                ▼
detect_missing_prices()        POST /api/data/update/prices
    │                                │
    ▼                                ▼
VCIClient.get_ohlcv()         update_prices_stream() [SSE]
    │
    ▼
INSERT OR IGNORE into stock_price_history
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
    │   └─ Input capital → POST optimize → Display 4 config cards
    │
    ├─ /portfolio
    │   └─ capital + pct params → GET portfolio → Position table
    │
    ├─ /verify
    │   └─ Input symbol → GET verify/{symbol} → Comparison table
    │
    └─ /data
        ├─ GET /api/data/health → DB coverage report
        └─ GET /api/data/update/prices/stream → SSE progress
```

### API Client Pattern

```typescript
// Centralized in frontend/src/lib/api.ts
export const api = {
  optimize: (capital) => fetchJson("/api/strategy/optimize", { capital }),
  portfolio: (capital, pct) => fetchJson("/api/strategy/portfolio", { capital, pct }),
  verify: (symbol) => fetchJson(`/api/verify/${symbol}`),
  streamPriceUpdate: (onEvent, onDone) => { /* SSE reader */ },
};
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
| PE5Y DB | SQLite | Single-file portability, no server needed, fast for read-heavy queries |
| PE5Y backend | FastAPI | Async support, auto-docs, Python ecosystem for finance |
| No ORM for PE5Y | Raw SQL | Performance-critical aggregate queries, complex GROUP BY/HAVING |
| Inventory DB | PostgreSQL + Prisma | Relational integrity, migrations, type-safe ORM |
| Frontend | Next.js 16 | React 19, App Router, Tailwind CSS integration |
| Config | Frozen dataclasses | Immutable, type-safe, env-var defaults |
| Rate limiting | Per-client throttle | Respect broker API limits (30 RPM each) |
| Data updates | SSE streaming | Real-time progress for long-running batch operations |
| Market cap filter | Dynamic by year | Accounts for VN market growth (10% per 2 years from 2015) |

## Ports & Services

| Service | Port | Protocol |
|---------|------|----------|
| PE5Y Backend (FastAPI) | 8002 | HTTP |
| Inventory Backend (Express) | 3001 | HTTP |
| Frontend (Next.js) | 3000 | HTTP |
| SEO Worker | - | Cloudflare Workers |
