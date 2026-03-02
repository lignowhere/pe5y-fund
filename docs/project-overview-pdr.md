# Project Overview & Product Development Requirements

> Last updated: 2026-03-02

## Project Identity

- **Name**: PE5Y Fund System
- **Type**: Quantitative investment tool for Vietnam stock market
- **Stage**: Active development (backend functional, frontend MVP)

## Problem Statement

Individual investors in Vietnam lack systematic tools to implement factor-based investing strategies. The PE5Y (Price-to-Earnings 5-Year Average) strategy requires:
- Aggregating financial data from multiple Vietnamese broker APIs
- Cross-validating data reliability across sources
- Computing complex signals with market cap, liquidity, and EPS filters
- Sizing portfolios with ADV (Average Daily Volume) constraints
- Backtesting with realistic cash flow scenarios

## Core Strategy

**PE5Y** = Select stocks with lowest P/E ratio based on 5-year average EPS.

1. **Signal generation**: Query 5-year annual EPS (all positive), apply market cap floor (dynamic by year, base 200B VND), apply liquidity filters
2. **Ranking**: Compute `pe_5y_avg = price / avg_eps_5y`, rank ascending
3. **Selection**: Top N% of universe (configurable: 10/12/14/16%)
4. **Rebalance**: Annually on September 1
5. **Position sizing**: Equal-weight with ADV-aware constraints (10% participation rate, 100-share lots)

## Sub-Projects

### 1. PE5Y Backend (Python/FastAPI) — Primary
- Strategy optimizer, signal generation, backtesting
- Data pipeline from VCI (Vietcap) and KBS (KB Securities) APIs
- SQLite database (`vietnam_stocks.db`) with price history and financial ratios
- Background scheduler for auto-updating missing data

### 2. PE5Y Frontend (Next.js/React) — MVP
- Dashboard: strategy optimization by capital input
- Portfolio viewer: position details with fill rates
- Data verification: VCI vs KBS cross-check
- Data management: status, missing data detection, streaming updates

### 3. Inventory Management Backend (Express/Prisma) — Separate
- Multi-channel inventory management system
- PostgreSQL with Prisma ORM
- Products, warehouses, inventory, movements, transfers, batch/lot tracking

### 4. SEO Automation (Cloudflare Workers) — Separate
- Automated SEO scanning with hybrid crawler (fetch + CF Browser)
- D1 database, Durable Objects for per-site coordination
- AI-powered rewrite engine using Claude
- Dashboard built with Vite/React

## Product Requirements

### P0 — Must Have
- [x] PE5Y signal generation with all filters
- [x] Strategy optimizer comparing select_pct configs
- [x] Portfolio sizing with ADV constraints
- [x] Data verification (VCI vs KBS cross-check)
- [x] Background data auto-update scheduler
- [x] Frontend dashboard with optimization flow
- [ ] Real-time portfolio tracking

### P1 — Should Have
- [x] Cash flow backtest (simulation + real data)
- [x] SSE streaming for price updates
- [x] Sensitivity heatmap data endpoint
- [ ] Historical yearly performance breakdown
- [ ] User authentication (JWT ready in Prisma schema)

### P2 — Nice to Have
- [ ] Multi-strategy comparison
- [ ] Alert/notification system
- [ ] Mobile-responsive optimization
- [ ] Export to CSV/PDF

## Technical Constraints

- **Data sources**: VCI GraphQL API (rate-limited 30 RPM), KBS REST API (rate-limited 30 RPM)
- **Database**: SQLite for PE5Y (single-file, portable), PostgreSQL for inventory
- **Price scale**: DB stores close prices in thousands of VND (`close * 1000 = VND`)
- **Market cap**: Column `market_cap_billions` is actually in VND (misleading name)
- **Formation years**: 2014-2024 (hold years 2015-2025)
- **EPS relaxation**: 3-year minimum for hold years 2015-2017, 5-year for 2018+

## Success Metrics

- PE5Y CAGR target: ~32% (Sep-1, 14% select, 2015-2025 backtest)
- Data coverage: 1500+ symbols across HSX, HNX, UPCOM
- Fill rate: >= 85% for recommended configuration
- API response time: < 2s for optimization queries
