# Project Overview & Product Development Requirements

> Last updated: 2026-03-04

## Project Identity

- **Name**: PE_TTM_20Q Fund System
- **Version**: 0.2.0
- **Type**: Quantitative investment tool for Vietnam stock market
- **Stage**: Active development (backend functional, frontend MVP complete)

## Problem Statement

Individual investors in Vietnam lack systematic tools to implement factor-based investing strategies. The PE_TTM_20Q strategy requires:
- Aggregating financial data from multiple Vietnamese broker APIs
- Cross-validating data reliability across sources
- Computing complex signals with market cap, liquidity, and quarterly EPS filters
- Sizing portfolios with ADV (Average Daily Volume) constraints
- Backtesting with realistic cash flow scenarios
- Calculating rebalance trades when depositing or withdrawing capital mid-cycle

## Core Strategy

**PE_TTM_20Q_RELAXED** (production) = Select stocks with lowest P/E ratio based on 20-quarter trailing average EPS, requiring only avg EPS > 0.

1. **Signal generation**: Query 20 quarters of quarterly EPS (`quarter=1-4` in `financial_ratios`), apply market cap floor (dynamic by year, base 200B VND), apply liquidity filters
2. **Ranking**: Compute `pe_ratio = price_vnd / avg_quarterly_eps[20q]`, rank ascending
3. **Selection**: Top N% of universe (configurable: 10/12/14/16%)
4. **Rebalance**: Annually on September 1
5. **Position sizing**: Equal-weight with ADV-aware constraints (10% participation rate, 100-share lots)

**Look-ahead bias prevention**: `_LATEST_Q_BY_MONTH` mapping enforces ~2-month reporting lag per quarter end.

### Signal Variants

| Variant | Filter | Universe | CAGR (2015-2025) |
|---------|--------|----------|------------------|
| PE_TTM_20Q_RELAXED | avg EPS > 0 | 21-26 stocks | **31.74%** (production) |
| PE_TTM_20Q (strict) | all 20 quarters positive | 11-12 stocks | — |
| PE5Y | 5-year annual EPS all positive | 11-12 stocks | 29.82% (reference) |
| VNINDEX | — | — | 8.66% (benchmark) |

> PE5Y code retained in `backend/strategy/signal.py` for reference and comparison backtests.
> KTPL adjustment (welfare fund leakage) tested and rejected: too noisy, -0.5pp impact.

## Sub-Projects

### 1. Fund Backend (Python/FastAPI) — Primary
- Strategy optimizer, signal generation, backtesting
- Data pipeline from VCI (Vietcap) and KBS (KB Securities) APIs
- SQLite database (`./vietnam_stocks.db`, local, gitignored) with price history and financial ratios
- Background scheduler for auto-updating missing data
- Live strategy config editor with JSON overrides (`strategy_config.json`)
- VNINDEX benchmark CAGR comparison
- Multi-strategy backtest engine (sensitivity runner: 12 months x 4 pcts x 3 strategies)

### 2. Fund Frontend (Next.js/React) — MVP Complete
- Dashboard: strategy optimization by capital input, VNINDEX benchmark display
- Portfolio viewer: position details with fill rates, sortable table, CSV export
- Rebalance Calculator: deposit/withdraw cash flow → buy/sell order list
- Config page: live strategy parameter editor (filters, sizing, costs, benchmark)
- Data verification: VCI vs KBS cross-check
- Data management: status, missing data detection, SSE streaming updates

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
- [x] PE_TTM_20Q_RELAXED signal generation with all filters
- [x] Strategy optimizer comparing select_pct configs
- [x] Portfolio sizing with ADV constraints
- [x] Data verification (VCI vs KBS cross-check)
- [x] Background data auto-update scheduler
- [x] Frontend dashboard with optimization flow
- [x] VNINDEX benchmark comparison
- [x] Live strategy config editor
- [x] Rebalance Calculator (deposit/withdraw → buy/sell orders)
- [ ] Real-time portfolio tracking

### P1 — Should Have
- [x] Cash flow backtest (simulation + real data)
- [x] SSE streaming for price + financials updates
- [x] Sensitivity heatmap data endpoint (72-run: 12 months x 4 pcts x 3 strategies)
- [x] Shared frontend formatter utilities (`fmtVND`, `fmtPrice`, `fillColor`)
- [x] Multi-strategy comparison (PE_TTM_20Q vs PE5Y vs PE_TTM_20Q_RELAXED)
- [x] Capital deployment simulation (add_existing vs fresh_signal — negligible diff < 0.4pp)
- [ ] Historical yearly performance breakdown (stub only)
- [ ] User authentication (JWT ready in Prisma schema)

### P2 — Nice to Have
- [ ] Alert/notification system
- [ ] Mobile-responsive optimization
- [ ] Export to CSV/PDF (partial: CSV copy on portfolio page)
- [ ] PDF strategy comparison report (scaffolded in `backend/report/pdf_report.py`)

## Technical Constraints

- **Data sources**: VCI GraphQL API (rate-limited 30 RPM), KBS REST API (rate-limited 30 RPM)
- **Database**: SQLite for fund (single-file, portable, gitignored), PostgreSQL for inventory
- **DB path**: Configured via `PE5Y_DB_PATH` env var; defaults to `./vietnam_stocks.db` (local)
- **Price scale**: DB stores close prices in thousands of VND (`close * 1000 = VND`)
- **Market cap**: Column `market_cap_billions` is actually in VND (misleading name)
- **Formation years**: 2014-2024 (hold years 2015-2025)
- **Quarterly EPS**: `financial_ratios` table with `quarter=1-4`; annual EPS uses `quarter=NULL`
- **Transaction costs**: 20 bps round-trip
- **Config overrides**: `strategy_config.json` adjacent to DB file; gitignored

## Success Metrics

- PE_TTM_20Q_RELAXED CAGR target: ~31.74% (Sep-1, 14% select, 2015-2025 backtest)
- Win rate: 88% (years beating VNINDEX)
- Data coverage: 1500+ symbols across HSX, HNX, UPCOM
- Fill rate: >= 85% for recommended configuration
- API response time: < 2s for optimization queries
