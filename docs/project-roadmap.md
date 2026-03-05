# Project Roadmap

> Last updated: 2026-03-04

## Completed (v0.2.0 — Current State)

### Signal Migration: PE5Y → PE_TTM_20Q_RELAXED
- [x] PE_TTM_20Q signal generator (`signal_pe_ttm_20q.py`) with `_LATEST_Q_BY_MONTH` look-ahead bias guard
- [x] PE_TTM_20Q_RELAXED: avg EPS > 0 filter → 21-26 stock universe (vs 11-12 PE5Y)
- [x] Production optimizer, API routes, position sizer switched to PE_TTM_20Q_RELAXED
- [x] `pe_5y_avg` → `pe_ratio` rename across backend + frontend (API v0.2.0 breaking change)
- [x] Sensitivity runner: 72-run sweep (12 months x 4 pcts x 3 strategies)
- [x] Capital deployment sim: add_existing vs fresh_signal — negligible diff (<0.4pp)
- [x] KTPL adjustment: tested, rejected (too noisy, -0.5pp)
- [x] PDF report scaffolded (`backend/report/pdf_report.py`)
- [x] Version bumped to 0.2.0; app title "PE_TTM_20Q Fund System"
- [x] PE5Y retained in `signal.py` for reference/comparison

### Backtest Results Confirmed
| Strategy | CAGR | Win Rate | Universe |
|----------|------|----------|---------|
| PE_TTM_20Q_RELAXED | 31.74% | 88% | 21-26 stocks |
| PE5Y | 29.82% | — | 11-12 stocks |
| VNINDEX | 8.66% | — | benchmark |

### Core PE_TTM_20Q Engine
- [x] Signal generation with quarterly EPS, market cap, liquidity filters
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
- [x] `/api/strategy/optimize` — config comparison + benchmark (PE_TTM_20Q_RELAXED)
- [x] `/api/strategy/portfolio` — position sizing with `pe_ratio` field
- [x] `/api/strategy/config` — live config CRUD
- [x] `/api/strategy/history/sensitivity` — 72-run heatmap data
- [x] `/api/data/health`, `/status`, `/search`
- [x] `/api/data/update/prices/stream`, `/update/financials/stream`
- [x] `/api/verify/{symbol}`, `/batch/check`

### Frontend (MVP Complete)
- [x] Dashboard — capital input, 4 config cards, VNINDEX benchmark
- [x] Portfolio — position table with `pe_ratio` column, sortable, CSV copy
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
- [ ] Win-rate display in optimizer results (already computed in sensitivity runner)

### User Management
- [ ] User authentication (Prisma schema has JWT-ready `User` model)
- [ ] Multi-user portfolio tracking

### Data Quality
- [ ] Automated data freshness alerts
- [ ] Retry logic improvements for VCI/KBS rate limit errors

### Reporting
- [ ] Complete PDF report (`backend/report/pdf_report.py` scaffolded)
- [ ] Export strategy comparison to CSV/PDF

## Medium-Term (P2)

### Portfolio Features
- [ ] Real-time portfolio tracking (live price feed)
- [ ] Position history tracking across rebalances

### Strategy Extensions
- [ ] Additional factor signals (PB, momentum, ROE-based)
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
| `sensitivity-pe5y-results.json` filename | Low | Filename references PE5Y but now contains multi-strategy data |
