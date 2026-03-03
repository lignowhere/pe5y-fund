# Project Roadmap

> Last updated: 2026-03-03

## Completed (Current State)

### Core PE5Y Engine
- [x] Signal generation with EPS, market cap, liquidity filters
- [x] Multi-config optimizer (10/12/14/16% select_pct)
- [x] ADV-aware equal-weight position sizer
- [x] Cash flow backtest (real data, 4 deployment strategies)
- [x] VNINDEX benchmark CAGR comparison

### Data Pipeline
- [x] VCI GraphQL client (financial ratios, OHLCV)
- [x] KBS REST client (profiles, financials)
- [x] VCI vs KBS cross-validation with tolerance thresholds
- [x] Background scheduler (6h auto-update)
- [x] SSE streaming for price + financials updates

### API Layer
- [x] `/api/strategy/optimize` — config comparison + benchmark
- [x] `/api/strategy/portfolio` — position sizing
- [x] `/api/strategy/config` — live config CRUD
- [x] `/api/data/health`, `/status`, `/search`
- [x] `/api/data/update/prices/stream`, `/update/financials/stream`
- [x] `/api/verify/{symbol}`, `/batch/check`

### Frontend (MVP Complete)
- [x] Dashboard — capital input, 4 config cards, VNINDEX benchmark
- [x] Portfolio — position table, sortable columns, CSV copy
- [x] Rebalance Calculator — deposit/withdraw → buy/sell order list
- [x] Config editor — live strategy parameter editor
- [x] Verify — VCI vs KBS comparison table
- [x] Data — DB health, missing data, streaming updates

### Infrastructure
- [x] Local SQLite DB (`./vietnam_stocks.db`, gitignored)
- [x] Root `.gitignore` (excludes DB, `.env`, `strategy_config.json`)
- [x] Shared frontend formatters (`fmtVND`, `fmtPrice`, `fillColor`)
- [x] No hardcoded absolute paths anywhere in codebase

## Near-Term (P1)

### Strategy Improvements
- [ ] Historical yearly performance breakdown endpoint (currently stub)
- [ ] Per-year CAGR table on dashboard

### User Management
- [ ] User authentication (Prisma schema has JWT-ready `User` model)
- [ ] Multi-user portfolio tracking

### Data Quality
- [ ] Automated data freshness alerts
- [ ] Retry logic improvements for VCI/KBS rate limit errors

## Medium-Term (P2)

### Portfolio Features
- [ ] Real-time portfolio tracking (live price feed)
- [ ] Export to CSV/PDF (partial: CSV copy exists on portfolio page)
- [ ] Position history tracking across rebalances

### Strategy Extensions
- [ ] Multi-strategy comparison (PE5Y vs PB, momentum, etc.)
- [ ] Custom factor weighting
- [ ] Alert/notification system for rebalance dates

### Frontend
- [ ] Mobile-responsive optimization
- [ ] Keyboard navigation improvements

## Long-Term (Stretch)

- [ ] Automated order execution via broker APIs
- [ ] Portfolio risk analytics (VaR, correlation matrix)
- [ ] Dividend tracking and reinvestment simulation
- [ ] Multi-account support

## Known Technical Debt

| Item | Impact | Notes |
|------|--------|-------|
| `market_cap_billions` column misnaming | Low | Actually stores VND, not billions; fix requires migration |
| `yearly_performance` endpoint is stub | Medium | Returns empty array |
| No test coverage | High | No unit/integration tests exist |
| Hardcoded Vietnamese labels in frontend | Low | No i18n support |
