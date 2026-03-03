"""Strategy API routes — optimizer, portfolio sizing, history."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..config import get_config
from ..strategy.benchmark import calc_benchmark_cagr
from ..strategy.optimizer import optimize
from ..strategy.position_sizer import portfolio_summary, size_portfolio
from ..strategy.signal import generate_signal, select_top_n

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


@router.get("/optimize")
def optimize_strategy(capital: float = 10_000_000_000, year: int | None = None):
    """Compare select_pct configs for given capital. Returns recommendation."""
    if capital <= 0:
        raise HTTPException(400, "Capital must be positive")
    _cfg = get_config()
    sc = _cfg.strategy
    results = optimize(capital, _cfg.db_path, _cfg, formation_year=year)

    # Benchmark buy-and-hold CAGR over same backtest period
    benchmark_cagr = calc_benchmark_cagr(
        _cfg.db_path,
        symbol=sc.benchmark_symbol,
        rebalance_month=sc.rebalance_month,
    )

    return {
        "results": [
            {
                "select_pct": r.select_pct,
                "stock_count": r.stock_count,
                "total_deployed_vnd": r.total_deployed_vnd,
                "cash_drag_pct": r.cash_drag_pct,
                "avg_fill_rate": r.avg_fill_rate,
                "max_days_needed": r.max_days_needed,
                "historical_cagr": r.historical_cagr,
                "recommended": r.recommended,
            }
            for r in results
        ],
        "benchmark": {
            "symbol": sc.benchmark_symbol,
            "cagr": benchmark_cagr,
        },
    }


@router.get("/portfolio")
def get_portfolio(capital: float = 10_000_000_000, pct: float = 14.0,
                  year: int | None = None):
    """Full portfolio with position sizing for given capital and select_pct."""
    if capital <= 0:
        raise HTTPException(400, "Capital must be positive")
    _cfg = get_config()
    import datetime
    fy = year or (datetime.date.today().year - 1)
    hold_year = fy + 1
    # Use rebalance-date pricing when Sep 1 of hold_year is in the past
    rebal_date = f"{hold_year}-09-01"
    today = datetime.date.today().isoformat()
    rebalance_date = rebal_date if rebal_date <= today else None

    candidates = generate_signal(
        _cfg.db_path, fy, _cfg,
        hold_year=hold_year, rebalance_date=rebalance_date,
    )
    selected = select_top_n(candidates, pct)
    symbols = [c.symbol for c in selected]

    sc = _cfg.strategy
    positions = size_portfolio(
        _cfg.db_path, symbols, capital,
        accum_days=sc.accum_days,
        participation_rate=sc.participation_rate,
        lot_size=sc.lot_size,
    )
    signal_map = {c.symbol: c for c in selected}
    for p in positions:
        sig = signal_map.get(p.symbol)
        if sig:
            p.pe_5y_avg = sig.pe_5y_avg
            p.signal_rank = sig.signal_rank

    summary = portfolio_summary(positions, capital)
    return {
        "formation_year": fy,
        "select_pct": pct,
        "capital_vnd": capital,
        "summary": summary,
        "positions": [
            {
                "symbol": p.symbol,
                "signal_rank": p.signal_rank,
                "pe_5y_avg": round(p.pe_5y_avg, 2) if p.pe_5y_avg else None,
                "current_price_vnd": p.current_price_vnd,
                "target_shares": p.target_shares,
                "target_value_vnd": p.target_value_vnd,
                "adv_shares": p.adv_shares,
                "shares_per_day": p.shares_per_day,
                "days_needed": p.days_needed,
                "fill_rate": p.fill_rate,
            }
            for p in sorted(positions, key=lambda x: x.signal_rank or 999)
        ],
    }


@router.get("/history/sensitivity")
def sensitivity_data():
    """72-run sensitivity heatmap data from backtest results."""
    _cfg = get_config()
    from ..strategy.optimizer import _load_sensitivity_data
    data = _load_sensitivity_data(_cfg.db_path.parent)
    if not data:
        return {"status": "no_data", "results": []}
    results = []
    for key, cagr in sorted(data.items()):
        parts = key.split("-")
        if len(parts) == 2:
            results.append({
                "month": int(parts[0]),
                "select_pct": float(parts[1]),
                "cagr_net": cagr,
            })
    return {"status": "ok", "results": results}


@router.get("/history/yearly")
def yearly_performance(pct: float = 14.0):
    """Yearly CAGR breakdown for given select_pct."""
    return {"select_pct": pct, "status": "stub", "years": []}
