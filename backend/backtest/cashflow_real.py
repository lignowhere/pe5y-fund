"""PE_TTM_20Q Cash Flow Backtest — REAL DATA from vietnam_stocks.db.

Uses actual PE_TTM_20Q signal generation and real stock prices.
Compares 3 strategies for handling mid-year deposits/withdrawals/dividends.

Formation years: 2015-2024 (rebalance Sep 1 each year)
"""
from __future__ import annotations

import math
import os
import random
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Ensure imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
os.environ.setdefault("PE5Y_DB_PATH", "./vietnam_stocks.db")

from backend.config import get_config
from backend.strategy.signal_pe_ttm_20q import generate_signal_20q, select_top_n_20q

# ── Parameters ─────────────────────────────────────────────────────

SEED = 42
SELECT_PCT = 14.0
LOT_SIZE = 100
TRANSACTION_COST_BPS = 10
CLOSE_SCALE_VND = 1000.0  # DB stores close in thousands

# Formation years: formation_year = hold_year - 1
# Original backtest: hold_year 2015..2025 → formation_year 2014..2024
# Rebalance Sep 1 of hold_year (= Sep 1 of formation_year + 1)
FORMATION_YEARS = list(range(2014, 2025))  # formation 2014..2024 → hold 2015..2025

# Cash event ranges
DEPOSITS_PER_YEAR = (2, 4)
DEPOSIT_RANGE_VND = (100_000_000, 2_000_000_000)
WITHDRAWALS_PER_YEAR = (1, 3)
WITHDRAWAL_RANGE_VND = (50_000_000, 500_000_000)
DIVIDEND_YIELD_ANNUAL = (0.02, 0.04)
DIVIDEND_MONTHS = [3, 6, 9, 12]

CASH_THRESHOLD_PCT = 5.0
CASH_TARGET_PCT = 2.0


# ── Data Classes ───────────────────────────────────────────────────

@dataclass
class Position:
    symbol: str
    shares: int
    avg_cost: float  # VND per share


@dataclass
class CashEvent:
    date: str         # YYYY-MM-DD
    event_type: str   # 'deposit', 'withdraw', 'dividend'
    amount: float     # VND


@dataclass
class YearResult:
    formation_year: int
    start_nav: float
    end_nav: float
    stock_count: int
    deposits: float
    withdrawals: float
    dividends: float
    trades: int
    avg_cash_pct: float
    max_drawdown: float
    twr_year: float     # single-year TWR


@dataclass
class ScenarioResult:
    name: str
    initial_nav: float
    final_nav: float
    total_deposits: float
    total_withdrawals: float
    total_dividends: float
    total_trades: int
    twr_annual: float
    mwrr_annual: float
    avg_cash_drag: float
    max_drawdown: float
    sharpe: float
    years: list[YearResult] = field(default_factory=list)


# ── DB Helpers ─────────────────────────────────────────────────────

def get_trading_dates(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    """Get sorted unique trading dates in range."""
    rows = conn.execute(
        """SELECT DISTINCT time FROM stock_price_history
           WHERE time >= ? AND time <= ? AND time GLOB '[0-9][0-9][0-9][0-9]-*'
           ORDER BY time""",
        (start, end),
    ).fetchall()
    return [r[0] for r in rows]


def get_prices_on_date(
    conn: sqlite3.Connection, symbols: list[str], date: str,
    forward: bool = True, max_gap_days: int = 10,
) -> dict[str, float]:
    """Get close price (VND) for symbols on or near a date.

    When forward=True (default), looks for first traded date on or after
    the target date within max_gap_days (matches original backtest).
    When forward=False, falls back to most recent price before date.
    """
    if not symbols:
        return {}
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(
        f"""SELECT symbol, close FROM stock_price_history
            WHERE symbol IN ({placeholders}) AND time = ?""",
        symbols + [date],
    ).fetchall()

    prices = {r[0]: r[1] * CLOSE_SCALE_VND for r in rows}

    # Fallback for missing symbols
    missing = [s for s in symbols if s not in prices]
    if missing:
        from datetime import datetime, timedelta
        dt = datetime.strptime(date, "%Y-%m-%d")

        if forward:
            end_dt = dt + timedelta(days=max_gap_days)
            end_date = end_dt.strftime("%Y-%m-%d")
            for sym in missing:
                row = conn.execute(
                    """SELECT close FROM stock_price_history
                       WHERE symbol = ? AND time >= ? AND time <= ?
                       AND close IS NOT NULL
                       ORDER BY time ASC LIMIT 1""",
                    (sym, date, end_date),
                ).fetchone()
                if row:
                    prices[sym] = row[0] * CLOSE_SCALE_VND
        else:
            start_dt = dt - timedelta(days=max_gap_days)
            start_date = start_dt.strftime("%Y-%m-%d")
            for sym in missing:
                row = conn.execute(
                    """SELECT close FROM stock_price_history
                       WHERE symbol = ? AND time >= ? AND time <= ?
                       AND close IS NOT NULL
                       ORDER BY time DESC LIMIT 1""",
                    (sym, date, start_date),
                ).fetchone()
                if row:
                    prices[sym] = row[0] * CLOSE_SCALE_VND

    return prices


def get_all_prices_in_range(
    conn: sqlite3.Connection, symbols: list[str], start: str, end: str
) -> dict[str, dict[str, float]]:
    """Get all prices for symbols in date range. Returns {date: {symbol: price_vnd}}."""
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(
        f"""SELECT time, symbol, close FROM stock_price_history
            WHERE symbol IN ({placeholders})
              AND time >= ? AND time <= ?
              AND time GLOB '[0-9][0-9][0-9][0-9]-*'
            ORDER BY time""",
        symbols + [start, end],
    ).fetchall()

    result: dict[str, dict[str, float]] = {}
    for r in rows:
        date, sym, close = r[0], r[1], r[2] * CLOSE_SCALE_VND
        if date not in result:
            result[date] = {}
        result[date][sym] = close

    return result


# ── Cash Event Generation ──────────────────────────────────────────

def generate_cash_events(
    hold_year: int,
    nav_estimate: float,
    rng: random.Random,
) -> list[CashEvent]:
    """Generate random cash events for the holding period (Sep hold_year to Aug hold_year+1)."""
    events: list[CashEvent] = []
    rebal_year = hold_year  # Sep of hold_year

    # Deposits
    for _ in range(rng.randint(*DEPOSITS_PER_YEAR)):
        month = rng.randint(1, 12)
        day = rng.randint(1, 28)
        yr = rebal_year if month >= 9 else rebal_year + 1
        date = f"{yr}-{month:02d}-{day:02d}"
        amount = rng.uniform(*DEPOSIT_RANGE_VND)
        events.append(CashEvent(date=date, event_type="deposit", amount=amount))

    # Withdrawals (capped at 10% NAV)
    for _ in range(rng.randint(*WITHDRAWALS_PER_YEAR)):
        month = rng.randint(1, 12)
        day = rng.randint(1, 28)
        yr = rebal_year if month >= 9 else rebal_year + 1
        date = f"{yr}-{month:02d}-{day:02d}"
        amount = min(rng.uniform(*WITHDRAWAL_RANGE_VND), nav_estimate * 0.10)
        events.append(CashEvent(date=date, event_type="withdraw", amount=amount))

    # Quarterly dividends
    div_yield = rng.uniform(*DIVIDEND_YIELD_ANNUAL)
    for q_month in DIVIDEND_MONTHS:
        yr = rebal_year if q_month >= 9 else rebal_year + 1
        date = f"{yr}-{q_month:02d}-15"
        amount = nav_estimate * div_yield / 4
        events.append(CashEvent(date=date, event_type="dividend", amount=amount))

    events.sort(key=lambda e: e.date)
    return events


# ── Portfolio Operations ───────────────────────────────────────────

def portfolio_value(positions: list[Position], prices: dict[str, float]) -> float:
    return sum(pos.shares * prices.get(pos.symbol, pos.avg_cost) for pos in positions)


def buy_equal_weight(
    cash: float,
    symbols: list[str],
    prices: dict[str, float],
) -> tuple[list[Position], float, int]:
    """Equal-weight allocation."""
    valid_symbols = [s for s in symbols if s in prices and prices[s] > 0]
    if not valid_symbols:
        return [], cash, 0

    per_stock = cash / len(valid_symbols)
    positions: list[Position] = []
    total_cost = 0.0
    trades = 0

    for sym in valid_symbols:
        price = prices[sym]
        shares = int(per_stock / price / LOT_SIZE) * LOT_SIZE
        if shares <= 0:
            shares = LOT_SIZE
        trade_value = shares * price
        fee = trade_value * TRANSACTION_COST_BPS / 10_000
        total_cost += trade_value + fee
        trades += 1
        positions.append(Position(symbol=sym, shares=shares, avg_cost=price))

    return positions, max(0.0, cash - total_cost), trades


def deploy_prorata(
    positions: list[Position],
    amount: float,
    prices: dict[str, float],
) -> tuple[float, int]:
    """Deploy cash pro-rata into existing positions."""
    if not positions or amount <= 0:
        return amount, 0

    per_stock = amount / len(positions)
    spent = 0.0
    trades = 0

    for pos in positions:
        price = prices.get(pos.symbol, pos.avg_cost)
        add_shares = int(per_stock / price / LOT_SIZE) * LOT_SIZE
        if add_shares <= 0:
            continue
        trade_value = add_shares * price
        fee = trade_value * TRANSACTION_COST_BPS / 10_000
        if spent + trade_value + fee > amount:
            break
        total_shares = pos.shares + add_shares
        pos.avg_cost = (pos.shares * pos.avg_cost + add_shares * price) / total_shares
        pos.shares = total_shares
        spent += trade_value + fee
        trades += 1

    return amount - spent, trades


def reduce_prorata(
    positions: list[Position],
    amount: float,
    prices: dict[str, float],
) -> tuple[float, int]:
    """Sell pro-rata from positions to raise cash."""
    if not positions or amount <= 0:
        return 0.0, 0

    per_stock = amount / len(positions)
    raised = 0.0
    trades = 0

    for pos in positions:
        price = prices.get(pos.symbol, pos.avg_cost)
        sell_shares = math.ceil(per_stock / price / LOT_SIZE) * LOT_SIZE
        sell_shares = min(sell_shares, pos.shares)
        if sell_shares <= 0:
            continue
        trade_value = sell_shares * price
        fee = trade_value * TRANSACTION_COST_BPS / 10_000
        pos.shares -= sell_shares
        raised += trade_value - fee
        trades += 1

    positions[:] = [p for p in positions if p.shares > 0]
    return raised, trades


# ── TWR / MWRR ────────────────────────────────────────────────────

def calc_twr(sub_returns: list[float], years: int) -> float:
    if not sub_returns or years <= 0:
        return 0.0
    cum = 1.0
    for r in sub_returns:
        cum *= (1 + r)
    return cum ** (1.0 / years) - 1


def calc_mwrr(initial: float, final: float, cfs: list[tuple[float, float]], years: int) -> float:
    total_cf = sum(cf for _, cf in cfs)
    gain = final - initial - total_cf
    weighted_cf = sum(cf * (1 - t / years) for t, cf in cfs)
    avg_cap = initial + weighted_cf
    if avg_cap <= 0:
        return 0.0
    return (1 + gain / avg_cap) ** (1.0 / years) - 1


def calc_sharpe(daily_returns: list[float], rf_annual: float = 0.05) -> float:
    if len(daily_returns) < 2:
        return 0.0
    mean_r = sum(daily_returns) / len(daily_returns)
    var = sum((r - mean_r)**2 for r in daily_returns) / len(daily_returns)
    std = math.sqrt(var) if var > 0 else 1e-10
    return ((mean_r * 252) - rf_annual) / (std * math.sqrt(252))


# ── Main Simulator ─────────────────────────────────────────────────

def simulate(
    name: str,
    deploy_mode: str,  # "none", "hold", "prorata", "threshold"
    initial_nav: float,
    formation_years: list[int],
    cash_events_by_year: dict[int, list[CashEvent]],
    cfg,
    conn: sqlite3.Connection,
) -> ScenarioResult:
    cash = initial_nav
    positions: list[Position] = []
    total_trades = 0
    total_deposits = 0.0
    total_withdrawals = 0.0
    total_dividends = 0.0
    year_results: list[YearResult] = []

    all_daily_returns: list[float] = []
    twr_sub_returns: list[float] = []
    mwrr_cashflows: list[tuple[float, float]] = []
    nav_after_last_cf = initial_nav
    overall_peak = initial_nav
    overall_max_dd = 0.0

    total_years = len(formation_years)

    for y_idx, fy in enumerate(formation_years):
        # formation_year = fy, hold_year = fy + 1
        # Rebalance Sep 1 of hold_year (original uses rebalance in hold_year)
        hold_year = fy + 1
        rebal_date = f"{hold_year}-09-01"
        period_end = f"{hold_year + 1}-08-31"

        # ── Generate PE_TTM_20Q signal (PE ranked by rebalance date price) ──
        candidates = generate_signal_20q(
            cfg.db_path, fy, cfg,
            hold_year=hold_year,
            rebalance_date=rebal_date,
            rebalance_month=9, require_all_positive=False,
        )
        selected = select_top_n_20q(candidates, SELECT_PCT)
        selected_symbols = [c.symbol for c in selected]

        # Get rebalance-day prices for BOTH old positions and new selections
        old_symbols = [pos.symbol for pos in positions]
        all_symbols = list(set(old_symbols + selected_symbols))
        rebal_prices = get_prices_on_date(conn, all_symbols, rebal_date)

        # TWR: sub-period return up to rebalance
        if positions:
            nav_before = portfolio_value(positions, rebal_prices) + cash
            if nav_after_last_cf > 0:
                twr_sub_returns.append(nav_before / nav_after_last_cf - 1)

            # Liquidate old positions (sell all at market price)
            for pos in positions:
                p = rebal_prices.get(pos.symbol, pos.avg_cost)
                sale = pos.shares * p
                fee = sale * TRANSACTION_COST_BPS / 10_000
                cash += sale - fee
                total_trades += 1

        # Buy new positions
        positions, cash, trades = buy_equal_weight(cash, selected_symbols, rebal_prices)
        total_trades += trades
        nav_after_last_cf = portfolio_value(positions, rebal_prices) + cash

        # ── Load all daily prices for this period ──
        all_syms = [p.symbol for p in positions]
        price_map = get_all_prices_in_range(conn, all_syms, rebal_date, period_end)
        trading_dates = sorted(price_map.keys())

        # ── Cash events for this period ──
        events = cash_events_by_year.get(fy, []) if deploy_mode != "none" else []
        event_idx = 0
        yr_deposits, yr_withdrawals, yr_dividends = 0.0, 0.0, 0.0
        yr_trades = trades

        # ── Daily loop ──
        prev_nav = nav_after_last_cf
        yr_peak = prev_nav
        yr_max_dd = 0.0
        cash_pcts: list[float] = []

        last_prices = rebal_prices.copy()

        for date in trading_dates:
            day_prices = price_map.get(date, {})
            # Merge with last known prices (forward fill)
            for sym in all_syms:
                if sym in day_prices:
                    last_prices[sym] = day_prices[sym]

            # Process cash events up to this date
            while event_idx < len(events) and events[event_idx].date <= date:
                ev = events[event_idx]
                event_idx += 1
                year_frac = y_idx + (trading_dates.index(date) / max(len(trading_dates), 1))

                # TWR: sub-period return before cash flow
                nav_before_cf = portfolio_value(positions, last_prices) + cash
                if nav_after_last_cf > 0:
                    twr_sub_returns.append(nav_before_cf / nav_after_last_cf - 1)

                if ev.event_type == "deposit":
                    cash += ev.amount
                    yr_deposits += ev.amount
                    mwrr_cashflows.append((year_frac, ev.amount))

                    if deploy_mode == "prorata" and positions:
                        deploy_amt = cash * 0.98
                        remaining, t = deploy_prorata(positions, deploy_amt, last_prices)
                        cash = cash - deploy_amt + remaining
                        yr_trades += t

                elif ev.event_type == "withdraw":
                    mwrr_cashflows.append((year_frac, -ev.amount))
                    if cash >= ev.amount:
                        cash -= ev.amount
                    elif positions:
                        needed = ev.amount - cash
                        raised, t = reduce_prorata(positions, needed, last_prices)
                        cash += raised
                        yr_trades += t
                        cash -= min(ev.amount, cash)
                    else:
                        cash = max(0, cash - ev.amount)
                    yr_withdrawals += ev.amount

                elif ev.event_type == "dividend":
                    cash += ev.amount
                    yr_dividends += ev.amount
                    mwrr_cashflows.append((year_frac, ev.amount))

                # Threshold check
                if deploy_mode == "threshold" and positions:
                    nav_now = portfolio_value(positions, last_prices) + cash
                    if nav_now > 0 and (cash / nav_now * 100) > CASH_THRESHOLD_PCT:
                        deploy_amt = cash - nav_now * CASH_TARGET_PCT / 100
                        if deploy_amt > 0:
                            remaining, t = deploy_prorata(positions, deploy_amt, last_prices)
                            cash = remaining
                            yr_trades += t

                nav_after_last_cf = portfolio_value(positions, last_prices) + cash

            # Daily NAV
            nav = portfolio_value(positions, last_prices) + cash
            cash_pcts.append(cash / nav * 100 if nav > 0 else 0)

            if prev_nav > 0:
                all_daily_returns.append(nav / prev_nav - 1)

            yr_peak = max(yr_peak, nav)
            dd = (yr_peak - nav) / yr_peak if yr_peak > 0 else 0
            yr_max_dd = max(yr_max_dd, dd)

            overall_peak = max(overall_peak, nav)
            o_dd = (overall_peak - nav) / overall_peak if overall_peak > 0 else 0
            overall_max_dd = max(overall_max_dd, o_dd)

            prev_nav = nav

        # Year-end
        end_nav = portfolio_value(positions, last_prices) + cash
        start_nav = portfolio_value(positions, rebal_prices) + cash if y_idx == 0 else year_results[-1].end_nav
        yr_twr = (end_nav / start_nav - 1) if start_nav > 0 else 0

        total_deposits += yr_deposits
        total_withdrawals += yr_withdrawals
        total_dividends += yr_dividends
        total_trades += yr_trades

        year_results.append(YearResult(
            formation_year=fy, start_nav=start_nav, end_nav=end_nav,
            stock_count=len(selected_symbols),
            deposits=yr_deposits, withdrawals=yr_withdrawals, dividends=yr_dividends,
            trades=yr_trades,
            avg_cash_pct=sum(cash_pcts) / len(cash_pcts) if cash_pcts else 0,
            max_drawdown=yr_max_dd * 100,
            twr_year=yr_twr * 100,
        ))

    # Final TWR sub-period
    final_nav = portfolio_value(positions, last_prices) + cash if positions else cash
    if nav_after_last_cf > 0:
        twr_sub_returns.append(final_nav / nav_after_last_cf - 1)

    twr = calc_twr(twr_sub_returns, total_years)
    mwrr = calc_mwrr(initial_nav, final_nav, mwrr_cashflows, total_years)
    sharpe = calc_sharpe(all_daily_returns)

    return ScenarioResult(
        name=name, initial_nav=initial_nav, final_nav=final_nav,
        total_deposits=total_deposits, total_withdrawals=total_withdrawals,
        total_dividends=total_dividends, total_trades=total_trades,
        twr_annual=twr, mwrr_annual=mwrr,
        avg_cash_drag=sum(yr.avg_cash_pct for yr in year_results) / len(year_results),
        max_drawdown=overall_max_dd * 100, sharpe=sharpe, years=year_results,
    )


# ── Formatting ─────────────────────────────────────────────────────

def fmt_vnd(v: float) -> str:
    return f"{v / 1e9:.2f}B"

def fmt_pct(v: float) -> str:
    return f"{v:.2f}%"


# ── Main ───────────────────────────────────────────────────────────

def run_backtest(seed: int = SEED):
    rng = random.Random(seed)
    cfg = get_config()

    initial_nav = rng.uniform(1e9, 10e9)
    formation_years = FORMATION_YEARS

    print(f"\n{'='*80}")
    print(f"  PE_TTM_20Q CASH FLOW BACKTEST — REAL DATA")
    print(f"{'='*80}")
    print(f"  DB: {cfg.db_path}")
    print(f"  Initial NAV: {fmt_vnd(initial_nav)} | select_pct: {SELECT_PCT}%")
    print(f"  Formation years: {formation_years[0]}..{formation_years[-1]} ({len(formation_years)} years)")
    print(f"  Rebalance: Sep 1 each year")
    print(f"{'='*80}\n")

    conn = sqlite3.connect(str(cfg.db_path))
    conn.row_factory = sqlite3.Row

    # Generate cash events (same for all scenarios)
    # Keyed by formation_year for consistency with the simulation loop
    cash_events_by_year: dict[int, list[CashEvent]] = {}
    nav_est = initial_nav
    print("CASH EVENTS (identical for all scenarios):")
    print(f"{'Hold':<6} {'Deposits':>12} {'Withdrawals':>14} {'Dividends':>12} {'#':>4}")
    print("-" * 52)

    for fy in formation_years:
        hold_year = fy + 1
        events = generate_cash_events(hold_year, nav_est, rng)
        cash_events_by_year[fy] = events
        deps = sum(e.amount for e in events if e.event_type == "deposit")
        withs = sum(e.amount for e in events if e.event_type == "withdraw")
        divs = sum(e.amount for e in events if e.event_type == "dividend")
        print(f"{hold_year:<6} {fmt_vnd(deps):>12} {fmt_vnd(withs):>14} {fmt_vnd(divs):>12} {len(events):>4}")
        nav_est = nav_est * 1.25 + deps - withs + divs  # rough estimate

    print()

    # Run scenarios
    scenarios = [
        ("Baseline (no CF)", "none"),
        ("Cash-hold (Sep 1)", "hold"),
        ("Pro-rata (now)", "prorata"),
        ("Threshold (>5%)", "threshold"),
    ]

    results: list[ScenarioResult] = []
    for name, mode in scenarios:
        print(f"Running: {name}...")
        r = simulate(name, mode, initial_nav, formation_years, cash_events_by_year, cfg, conn)
        results.append(r)

    # ── Results Table ──
    print(f"\n{'='*80}")
    print(f"  RESULTS — TWR = investment skill, MWRR = investor return")
    print(f"{'='*80}\n")

    hdr = f"{'Scenario':<22} {'Final NAV':>10} {'TWR':>8} {'MWRR':>8} {'Sharpe':>7} {'Cash%':>7} {'MaxDD':>7} {'Trades':>7}"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        print(
            f"{r.name:<22} {fmt_vnd(r.final_nav):>10} "
            f"{fmt_pct(r.twr_annual * 100):>8} {fmt_pct(r.mwrr_annual * 100):>8} "
            f"{r.sharpe:>7.2f} {fmt_pct(r.avg_cash_drag):>7} "
            f"{fmt_pct(r.max_drawdown):>7} {r.total_trades:>7}"
        )

    # ── TWR Delta ──
    print(f"\n{'='*80}")
    print(f"  TWR DELTA vs BASELINE")
    print(f"{'='*80}\n")
    baseline_twr = results[0].twr_annual
    print(f"  Baseline TWR: {fmt_pct(baseline_twr * 100)}")
    for r in results[1:]:
        delta = (r.twr_annual - baseline_twr) * 100
        print(f"  {r.name:<22} TWR: {fmt_pct(r.twr_annual * 100):>8}  delta: {delta:+.2f}%")

    # ── Year-by-Year ──
    print(f"\n{'='*80}")
    print(f"  YEAR-BY-YEAR COMPARISON")
    print(f"{'='*80}\n")

    h2 = f"{'Year':<6} {'#stk':>5} | {'Baseline':>8} | {'Hold':>8} | {'Pro-rata':>8} | {'Thresh':>8}"
    print(h2)
    print("-" * len(h2))
    for i in range(len(formation_years)):
        yr = formation_years[i]
        vals = [f"{r.years[i].twr_year:>7.1f}%" for r in results]
        n = results[0].years[i].stock_count
        print(f"{yr:<6} {n:>5} | {vals[0]} | {vals[1]} | {vals[2]} | {vals[3]}")

    # ── Pro-rata Detail ──
    print(f"\n{'='*80}")
    print(f"  PRO-RATA DETAILS")
    print(f"{'='*80}\n")

    prorata = results[2]
    h3 = f"{'Year':<6} {'Start':>10} {'End':>10} {'Dep':>8} {'With':>8} {'Div':>8} {'Cash%':>7} {'DD':>7}"
    print(h3)
    print("-" * len(h3))
    for yr in prorata.years:
        print(
            f"{yr.formation_year:<6} {fmt_vnd(yr.start_nav):>10} {fmt_vnd(yr.end_nav):>10} "
            f"{fmt_vnd(yr.deposits):>8} {fmt_vnd(yr.withdrawals):>8} {fmt_vnd(yr.dividends):>8} "
            f"{fmt_pct(yr.avg_cash_pct):>7} {fmt_pct(yr.max_drawdown):>7}"
        )

    # ── Original-Style Validation ──
    # Compute per-stock returns like original backtest to validate signal correctness
    print(f"\n{'='*80}")
    print(f"  ORIGINAL-STYLE VALIDATION (per-stock return avg, no cash flows)")
    print(f"{'='*80}\n")

    annual_rets: list[float] = []
    for fy in formation_years:
        hold_yr = fy + 1
        buy_date = f"{hold_yr}-09-01"
        sell_date = f"{hold_yr + 1}-09-01"

        cands = generate_signal_20q(cfg.db_path, fy, cfg, hold_year=hold_yr, rebalance_date=buy_date,
                                    rebalance_month=9, require_all_positive=False)
        sel = select_top_n_20q(cands, SELECT_PCT)
        sel_syms = [c.symbol for c in sel]
        if not sel_syms:
            annual_rets.append(0.0)
            continue

        bp = get_prices_on_date(conn, sel_syms, buy_date, forward=True)
        sp = get_prices_on_date(conn, sel_syms, sell_date, forward=True)

        stock_rets = []
        for sym in sel_syms:
            b = bp.get(sym)
            s = sp.get(sym)
            if b and s and b > 0:
                ret = s / b - 1.0
                # Apply transaction cost (10 bps per side = 20 bps round trip)
                net_ret = ret - TRANSACTION_COST_BPS * 2 / 10_000
                stock_rets.append(net_ret)

        port_ret = sum(stock_rets) / len(stock_rets) if stock_rets else 0.0
        annual_rets.append(port_ret)
        n_stocks = len(stock_rets)
        print(f"  Hold {hold_yr}: {n_stocks:>3} stocks | port_ret = {port_ret*100:>7.2f}%")

    cum = 1.0
    for r in annual_rets:
        cum *= (1 + r)
    n_yrs = len(annual_rets)
    cagr_simple = cum ** (1.0 / n_yrs) - 1 if n_yrs > 0 else 0.0
    print(f"\n  Cumulative: {cum:.4f}x over {n_yrs} years")
    print(f"  CAGR (original-style): {cagr_simple*100:.2f}%")
    print(f"  Reference (sensitivity): ~32.65% for Sep 1, 14%")

    # ── Conclusion ──
    print(f"\n{'='*80}")
    print(f"  CONCLUSION")
    print(f"{'='*80}\n")

    best = max(results[1:], key=lambda r: r.twr_annual)
    worst = min(results[1:], key=lambda r: r.twr_annual)
    print(f"  Baseline (pure PE_TTM_20Q):  TWR {fmt_pct(baseline_twr * 100)}")
    print(f"  Best with cash flows:  {best.name} TWR {fmt_pct(best.twr_annual * 100)}")
    print(f"  Worst with cash flows: {worst.name} TWR {fmt_pct(worst.twr_annual * 100)}")
    print(f"  TWR gap (best-worst):  {fmt_pct((best.twr_annual - worst.twr_annual) * 100)}")
    print(f"\n  Cash drag: Pro-rata {fmt_pct(prorata.avg_cash_drag)} vs "
          f"Hold {fmt_pct(results[1].avg_cash_drag)} vs "
          f"Threshold {fmt_pct(results[3].avg_cash_drag)}")
    print()

    conn.close()
    return results


if __name__ == "__main__":
    run_backtest()
