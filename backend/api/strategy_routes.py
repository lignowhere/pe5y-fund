"""Strategy API routes — optimizer, portfolio sizing, history."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..config import get_config
from ..strategy.benchmark import calc_benchmark_cagr
from ..strategy.optimizer import optimize
from ..strategy.position_sizer import portfolio_summary, size_portfolio
from ..strategy.signal_pe_ttm_20q import generate_signal_20q, select_top_n_20q

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


STRATEGY_PARAMS = {
    "TTM_20Q": {"require_all_positive": False, "require_last_n_positive": 0},
    "LAST_8Q_PLUS": {"require_all_positive": False, "require_last_n_positive": 8},
}


@router.get("/optimize")
def optimize_strategy(capital: float = 10_000_000_000, year: int | None = None,
                      month: int | None = None, strategy: str = "TTM_20Q"):
    """Compare select_pct configs for given capital. Returns recommendation."""
    if capital <= 0:
        raise HTTPException(400, "Capital must be positive")
    if month is not None and not (1 <= month <= 12):
        raise HTTPException(400, "Month must be 1-12")
    if strategy not in STRATEGY_PARAMS:
        raise HTTPException(400, f"Unknown strategy: {strategy}")
    _cfg = get_config()
    sc = _cfg.strategy
    rebal_month = month or sc.rebalance_month
    strat_kw = STRATEGY_PARAMS[strategy]
    results = optimize(capital, _cfg.db_path, _cfg,
                       formation_year=year, rebalance_month=rebal_month,
                       **strat_kw)

    benchmark_cagr = calc_benchmark_cagr(
        _cfg.db_path,
        symbol=sc.benchmark_symbol,
        rebalance_month=rebal_month,
    )

    return {
        "rebalance_month": rebal_month,
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
                  year: int | None = None, month: int | None = None,
                  strategy: str = "TTM_20Q"):
    """Full portfolio with position sizing for given capital and select_pct."""
    if capital <= 0:
        raise HTTPException(400, "Capital must be positive")
    if month is not None and not (1 <= month <= 12):
        raise HTTPException(400, "Month must be 1-12")
    if strategy not in STRATEGY_PARAMS:
        raise HTTPException(400, f"Unknown strategy: {strategy}")
    _cfg = get_config()
    import datetime
    fy = year or (datetime.date.today().year - 1)
    hold_year = fy + 1
    sc = _cfg.strategy
    rebal_month = month or sc.rebalance_month
    rebal_date = f"{hold_year}-{rebal_month:02d}-01"
    today = datetime.date.today().isoformat()
    rebalance_date = rebal_date if rebal_date <= today else None

    strat_kw = STRATEGY_PARAMS[strategy]
    candidates = generate_signal_20q(
        _cfg.db_path, fy, _cfg,
        hold_year=hold_year, rebalance_date=rebalance_date,
        rebalance_month=rebal_month, **strat_kw,
    )
    selected = select_top_n_20q(candidates, pct)
    symbols = [c.symbol for c in selected]

    positions = size_portfolio(
        _cfg.db_path, symbols, capital,
        accum_days=sc.accum_days,
        participation_rate=sc.participation_rate,
        lot_size=sc.lot_size,
    )
    # Map signal data + buy_price → positions, compute gain
    signal_map = {c.symbol: c for c in selected}
    for p in positions:
        sig = signal_map.get(p.symbol)
        if sig:
            p.pe_ratio = sig.pe_ttm_20q
            p.signal_rank = sig.signal_rank
            p.buy_price_vnd = sig.buy_price_vnd
            if sig.buy_price_vnd and sig.buy_price_vnd > 0:
                p.gain_pct = round(
                    (p.current_price_vnd / sig.buy_price_vnd - 1) * 100, 2
                )

    summary = portfolio_summary(positions, capital)

    # --- Accumulation monitoring ---
    PRICE_CAP_PCT = 20.0    # skip if price up > 20%
    PRICE_WATCH_PCT = 10.0  # watch if price up > 10%
    pe_threshold = selected[-1].pe_ttm_20q if selected else None
    has_rebalance = rebalance_date is not None  # only monitor post-rebalance

    buy_count = watch_count = skip_count = 0
    position_list = []
    for p in sorted(positions, key=lambda x: x.signal_rank or 999):
        sig = signal_map.get(p.symbol)
        gain = p.gain_pct or 0
        current_pe = None
        accum_status = "BUY"
        skip_reason = None

        if sig and sig.avg_eps_20q > 0:
            current_pe = round(p.current_price_vnd / (sig.avg_eps_20q * 4), 2)

        if has_rebalance and p.buy_price_vnd:
            if gain > PRICE_CAP_PCT:
                accum_status = "SKIP"
                skip_reason = "PRICE_CAP"
                skip_count += 1
            elif current_pe and pe_threshold and current_pe > pe_threshold:
                accum_status = "SKIP"
                skip_reason = "PE_DROPPED"
                skip_count += 1
            elif gain > PRICE_WATCH_PCT:
                accum_status = "WATCH"
                watch_count += 1
            else:
                buy_count += 1
        else:
            buy_count += 1

        position_list.append({
            "symbol": p.symbol,
            "signal_rank": p.signal_rank,
            "pe_ratio": round(p.pe_ratio, 2) if p.pe_ratio else None,
            "current_price_vnd": p.current_price_vnd,
            "buy_price_vnd": p.buy_price_vnd,
            "gain_pct": p.gain_pct,
            "target_shares": p.target_shares,
            "target_value_vnd": p.target_value_vnd,
            "adv_shares": p.adv_shares,
            "shares_per_day": p.shares_per_day,
            "days_needed": p.days_needed,
            "fill_rate": p.fill_rate,
            "current_pe": current_pe,
            "accum_status": accum_status,
            "skip_reason": skip_reason,
        })

    # Waitlist: next candidates below the cutoff
    n_sel = len(selected)
    waitlist = []
    for c in candidates[n_sel:n_sel + 10]:
        waitlist.append({
            "symbol": c.symbol,
            "signal_rank": c.signal_rank,
            "pe_ratio": round(c.pe_ttm_20q, 2),
            "buy_price_vnd": c.buy_price_vnd,
        })

    return {
        "formation_year": fy,
        "select_pct": pct,
        "capital_vnd": capital,
        "strategy": strategy,
        "rebalance_date": rebalance_date or rebal_date,
        "price_basis": "adjusted",
        "summary": summary,
        "positions": position_list,
        "accum_monitor": {
            "price_cap_pct": PRICE_CAP_PCT,
            "pe_threshold": round(pe_threshold, 2) if pe_threshold else None,
            "buy_count": buy_count,
            "watch_count": watch_count,
            "skip_count": skip_count,
        },
        "waitlist": waitlist,
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
