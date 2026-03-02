"""PE5Y Cash Flow Backtest — Simulation with Synthetic Data.

Compares 3 strategies for handling mid-year cash flows:
  1. Cash-hold: Cash events happen but idle cash waits until Sep 1
  2. Pro-rata:  Cash deployed/withdrawn immediately into existing positions
  3. Threshold: Cash accumulated, deployed when > 5% NAV

ALL scenarios receive identical cash events and use the same price data.
Baseline (no cash flows) shown separately as pure investment return reference.

Metrics:
  - TWR (Time-Weighted Return): measures pure investment skill, strips out cash flow timing
  - MWRR (Money-Weighted Return): measures actual investor experience
  - Sharpe, Max Drawdown, Cash Drag
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# ── Simulation Parameters ──────────────────────────────────────────

SEED = 42
NUM_STOCKS = 30
LOT_SIZE = 100
TRANSACTION_COST_BPS = 10       # 0.10%
ANNUAL_MEAN_RETURN = 0.25       # 25% — PE5Y-like strategy return
ANNUAL_VOLATILITY = 0.30        # 30% annual volatility
SIM_YEARS = 10                  # 2015-2024
TRADING_DAYS_PER_YEAR = 250
REBALANCE_MONTH = 9             # September
CASH_THRESHOLD_PCT = 5.0        # Auto-deploy when cash > 5% NAV
CASH_TARGET_PCT = 2.0           # Keep 2% as buffer after deploy

# Cash event ranges
DEPOSITS_PER_YEAR = (2, 4)
DEPOSIT_RANGE_VND = (100_000_000, 2_000_000_000)   # 100M - 2B
WITHDRAWALS_PER_YEAR = (1, 3)
WITHDRAWAL_RANGE_VND = (50_000_000, 500_000_000)    # 50M - 500M
DIVIDEND_YIELD_ANNUAL = (0.02, 0.04)                # 2-4%
DIVIDEND_QUARTERS = [3, 6, 9, 12]                    # Mar, Jun, Sep, Dec


# ── Data Classes ───────────────────────────────────────────────────

@dataclass
class Position:
    symbol: str
    shares: int
    avg_cost: float  # VND per share

    @property
    def cost_basis(self) -> float:
        return self.shares * self.avg_cost


@dataclass
class CashEvent:
    day: int         # trading day within year (0-249)
    month: int       # calendar month (1-12)
    event_type: str  # 'deposit', 'withdraw', 'dividend'
    amount: float    # VND (positive always)


@dataclass
class YearResult:
    year: int
    start_nav: float
    end_nav: float
    deposits: float
    withdrawals: float
    dividends: float
    trades: int
    avg_cash_pct: float
    max_drawdown: float


@dataclass
class ScenarioResult:
    name: str
    initial_nav: float
    final_nav: float
    total_deposits: float
    total_withdrawals: float
    total_dividends: float
    total_trades: int
    twr_annual: float       # Time-Weighted Return (annualized) — investment skill
    mwrr_annual: float      # Money-Weighted Return (annualized) — investor experience
    avg_cash_drag: float
    max_drawdown: float
    sharpe: float
    years: list[YearResult] = field(default_factory=list)


# ── Synthetic Price Generation ─────────────────────────────────────

def generate_stock_prices(
    num_stocks: int,
    num_days: int,
    initial_price: float = 30_000.0,
    mu: float = ANNUAL_MEAN_RETURN,
    sigma: float = ANNUAL_VOLATILITY,
    rng: random.Random | None = None,
) -> list[list[float]]:
    """Generate daily prices for N stocks using geometric Brownian motion."""
    if rng is None:
        rng = random.Random(SEED)

    dt = 1.0 / TRADING_DAYS_PER_YEAR
    all_prices: list[list[float]] = []

    for i in range(num_stocks):
        stock_mu = mu + rng.gauss(0, 0.05)
        stock_sigma = sigma + rng.gauss(0, 0.05)
        stock_sigma = max(0.10, stock_sigma)

        price = initial_price * (0.5 + rng.random())
        prices = [price]

        for _ in range(num_days - 1):
            z = rng.gauss(0, 1)
            daily_return = (stock_mu - 0.5 * stock_sigma**2) * dt + stock_sigma * math.sqrt(dt) * z
            price *= math.exp(daily_return)
            price = max(price, 1000.0)
            prices.append(price)

        all_prices.append(prices)

    return all_prices


# ── Cash Event Generation ──────────────────────────────────────────

def generate_cash_events(
    nav_estimate: float,
    rng: random.Random,
) -> list[CashEvent]:
    """Generate random deposits, withdrawals, and dividends for one year."""
    events: list[CashEvent] = []

    n_deposits = rng.randint(*DEPOSITS_PER_YEAR)
    for _ in range(n_deposits):
        day = rng.randint(0, TRADING_DAYS_PER_YEAR - 1)
        month = min(1 + int(day / TRADING_DAYS_PER_YEAR * 12), 12)
        amount = rng.uniform(*DEPOSIT_RANGE_VND)
        events.append(CashEvent(day=day, month=month, event_type="deposit", amount=amount))

    n_withdrawals = rng.randint(*WITHDRAWALS_PER_YEAR)
    for _ in range(n_withdrawals):
        day = rng.randint(0, TRADING_DAYS_PER_YEAR - 1)
        month = min(1 + int(day / TRADING_DAYS_PER_YEAR * 12), 12)
        amount = min(rng.uniform(*WITHDRAWAL_RANGE_VND), nav_estimate * 0.10)
        events.append(CashEvent(day=day, month=month, event_type="withdraw", amount=amount))

    div_yield = rng.uniform(*DIVIDEND_YIELD_ANNUAL)
    quarterly_yield = div_yield / 4
    for q_month in DIVIDEND_QUARTERS:
        day = min(int((q_month - 0.5) / 12 * TRADING_DAYS_PER_YEAR), TRADING_DAYS_PER_YEAR - 1)
        amount = nav_estimate * quarterly_yield
        events.append(CashEvent(day=day, month=q_month, event_type="dividend", amount=amount))

    events.sort(key=lambda e: e.day)
    return events


# ── Portfolio Operations ───────────────────────────────────────────

def buy_equal_weight(
    cash: float,
    prices: list[float],
    num_stocks: int,
    lot_size: int = LOT_SIZE,
    cost_bps: float = TRANSACTION_COST_BPS,
) -> tuple[list[Position], float, int]:
    """Allocate cash equally across num_stocks."""
    per_stock = cash / num_stocks
    positions: list[Position] = []
    total_cost = 0.0
    trades = 0

    for i in range(num_stocks):
        price = prices[i]
        shares = int(per_stock / price / lot_size) * lot_size
        if shares <= 0:
            shares = lot_size
        trade_value = shares * price
        fee = trade_value * cost_bps / 10_000
        total_cost += trade_value + fee
        trades += 1
        positions.append(Position(symbol=f"STK{i:03d}", shares=shares, avg_cost=price))

    return positions, max(0.0, cash - total_cost), trades


def deploy_prorata(
    positions: list[Position],
    amount: float,
    prices: list[float],
    lot_size: int = LOT_SIZE,
    cost_bps: float = TRANSACTION_COST_BPS,
) -> tuple[float, int]:
    """Deploy cash pro-rata into existing positions. Returns remaining cash, trades."""
    if not positions or amount <= 0:
        return amount, 0

    per_stock = amount / len(positions)
    spent = 0.0
    trades = 0

    for i, pos in enumerate(positions):
        price = prices[i]
        add_shares = int(per_stock / price / lot_size) * lot_size
        if add_shares <= 0:
            continue
        trade_value = add_shares * price
        fee = trade_value * cost_bps / 10_000
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
    prices: list[float],
    lot_size: int = LOT_SIZE,
    cost_bps: float = TRANSACTION_COST_BPS,
) -> tuple[float, int]:
    """Sell pro-rata from existing positions to raise cash."""
    if not positions or amount <= 0:
        return 0.0, 0

    per_stock = amount / len(positions)
    raised = 0.0
    trades = 0

    for i, pos in enumerate(positions):
        price = prices[i]
        sell_shares = math.ceil(per_stock / price / lot_size) * lot_size
        sell_shares = min(sell_shares, pos.shares)
        if sell_shares <= 0:
            continue
        trade_value = sell_shares * price
        fee = trade_value * cost_bps / 10_000
        pos.shares -= sell_shares
        raised += trade_value - fee
        trades += 1

    positions[:] = [p for p in positions if p.shares > 0]
    return raised, trades


def portfolio_value(positions: list[Position], prices: list[float]) -> float:
    """Current market value of all positions."""
    return sum(pos.shares * prices[i] for i, pos in enumerate(positions))


# ── TWR Calculation ────────────────────────────────────────────────

def calc_twr(sub_period_returns: list[float], years: int) -> float:
    """Annualized Time-Weighted Return from chain-linked sub-period returns.

    TWR isolates investment performance from cash flow timing.
    Each sub-period return = NAV_before_next_cf / NAV_after_prev_cf
    """
    if not sub_period_returns or years <= 0:
        return 0.0
    cumulative = 1.0
    for r in sub_period_returns:
        cumulative *= (1 + r)
    return cumulative ** (1.0 / years) - 1


def calc_mwrr(
    initial_nav: float,
    final_nav: float,
    cashflows: list[tuple[float, float]],  # (year_fraction, amount) — positive=in, negative=out
    years: int,
) -> float:
    """Money-Weighted Return using modified Dietz method.

    MWRR reflects the actual investor return including cash flow timing.
    """
    total_cf = sum(cf for _, cf in cashflows)
    gain = final_nav - initial_nav - total_cf

    # Weighted cash flows (weight by time remaining)
    weighted_cf = sum(cf * (1 - t / years) for t, cf in cashflows)
    avg_capital = initial_nav + weighted_cf

    if avg_capital <= 0:
        return 0.0

    # Annualize the modified Dietz return
    period_return = gain / avg_capital
    annual_return = (1 + period_return) ** (1.0 / years) - 1
    return annual_return


# ── Sharpe & Drawdown ──────────────────────────────────────────────

def calc_sharpe(daily_navs: list[float], risk_free_annual: float = 0.05) -> float:
    """Annualized Sharpe from daily NAV (excluding days with cash flows)."""
    if len(daily_navs) < 2:
        return 0.0
    daily_returns = [daily_navs[i] / daily_navs[i-1] - 1 for i in range(1, len(daily_navs))]
    mean_ret = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_ret)**2 for r in daily_returns) / len(daily_returns)
    std_ret = math.sqrt(variance) if variance > 0 else 1e-10
    return ((mean_ret * 252) - risk_free_annual) / (std_ret * math.sqrt(252))


def calc_max_drawdown(daily_navs: list[float]) -> float:
    """Max drawdown as percentage."""
    if not daily_navs:
        return 0.0
    peak = daily_navs[0]
    max_dd = 0.0
    for nav in daily_navs:
        peak = max(peak, nav)
        dd = (peak - nav) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
    return max_dd * 100


# ── Generic Simulator ──────────────────────────────────────────────

def simulate(
    name: str,
    initial_nav: float,
    all_prices: list[list[float]],
    cash_events_by_year: list[list[CashEvent]],
    years: int,
    deploy_mode: str,  # "hold", "prorata", "threshold", "none"
) -> ScenarioResult:
    """Unified simulator for all strategies.

    deploy_mode:
      "none"      — no cash events at all (baseline)
      "hold"      — cash sits idle until next Sep 1 rebalance
      "prorata"   — deposits deployed immediately pro-rata
      "threshold" — cash deployed when > 5% NAV
    """
    days_per_year = TRADING_DAYS_PER_YEAR
    cash = initial_nav
    positions: list[Position] = []
    total_trades = 0
    total_deposits = 0.0
    total_withdrawals = 0.0
    total_dividends = 0.0
    year_results: list[YearResult] = []
    daily_navs: list[float] = []

    # For TWR: track sub-period returns between cash flow events
    twr_sub_returns: list[float] = []
    nav_after_last_cf = initial_nav  # NAV right after the last cash flow

    # For MWRR: track (year_fraction, signed_amount) of each cash flow
    mwrr_cashflows: list[tuple[float, float]] = []

    for y in range(years):
        day_offset = y * days_per_year
        rebal_day = int((REBALANCE_MONTH - 1) / 12 * days_per_year)
        events = cash_events_by_year[y] if deploy_mode != "none" else []
        yr_deposits, yr_withdrawals, yr_dividends = 0.0, 0.0, 0.0
        yr_trades = 0

        # ── Sep 1 Annual Rebalance ──
        prices_at_rebal = [p[day_offset + rebal_day] for p in all_prices]

        if positions:
            # TWR: record sub-period return up to rebalance
            nav_before = portfolio_value(positions, prices_at_rebal) + cash
            if nav_after_last_cf > 0:
                twr_sub_returns.append(nav_before / nav_after_last_cf - 1)

            # Liquidate
            cash += portfolio_value(positions, prices_at_rebal)
            fee = portfolio_value(positions, prices_at_rebal) * TRANSACTION_COST_BPS / 10_000
            cash -= fee
            yr_trades += len(positions)

        # Buy new
        positions, cash, trades = buy_equal_weight(cash, prices_at_rebal, NUM_STOCKS)
        yr_trades += trades
        nav_after_last_cf = portfolio_value(positions, prices_at_rebal) + cash

        # ── Process cash events ──
        for ev in events:
            day_idx = min(ev.day, days_per_year - 1)
            day_prices = [p[day_offset + day_idx] for p in all_prices]
            year_frac = y + day_idx / days_per_year  # fractional year for MWRR

            # TWR: record sub-period return BEFORE this cash flow
            nav_before_cf = portfolio_value(positions, day_prices) + cash
            if nav_after_last_cf > 0:
                twr_sub_returns.append(nav_before_cf / nav_after_last_cf - 1)

            if ev.event_type == "deposit":
                cash += ev.amount
                yr_deposits += ev.amount
                mwrr_cashflows.append((year_frac, ev.amount))

                if deploy_mode == "prorata" and positions:
                    deploy_amt = cash * 0.98  # keep 2% buffer
                    remaining, t = deploy_prorata(positions, deploy_amt, day_prices)
                    cash = cash - deploy_amt + remaining
                    yr_trades += t

                elif deploy_mode == "threshold":
                    pass  # threshold check below

            elif ev.event_type == "withdraw":
                mwrr_cashflows.append((year_frac, -ev.amount))
                if cash >= ev.amount:
                    cash -= ev.amount
                elif positions:
                    needed = ev.amount - cash
                    raised, t = reduce_prorata(positions, needed, day_prices)
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

            # Threshold check (applies to all event types in threshold mode)
            if deploy_mode == "threshold" and positions:
                nav_now = portfolio_value(positions, day_prices) + cash
                cash_pct = cash / nav_now * 100 if nav_now > 0 else 0
                if cash_pct > CASH_THRESHOLD_PCT:
                    deploy_amt = cash - nav_now * CASH_TARGET_PCT / 100
                    if deploy_amt > 0:
                        remaining, t = deploy_prorata(positions, deploy_amt, day_prices)
                        cash = remaining
                        yr_trades += t

            # TWR: update nav_after_last_cf to current NAV (after cash flow + any deployment)
            nav_after_last_cf = portfolio_value(positions, day_prices) + cash

        # ── Track daily NAV for Sharpe & Drawdown ──
        cash_pcts: list[float] = []
        year_daily_navs: list[float] = []

        for d in range(days_per_year):
            day_prices = [p[day_offset + d] for p in all_prices]
            nav = portfolio_value(positions, day_prices) + cash
            daily_navs.append(nav)
            year_daily_navs.append(nav)
            cash_pcts.append(cash / nav * 100 if nav > 0 else 0)

        end_prices = [p[day_offset + days_per_year - 1] for p in all_prices]
        end_nav = portfolio_value(positions, end_prices) + cash

        total_deposits += yr_deposits
        total_withdrawals += yr_withdrawals
        total_dividends += yr_dividends
        total_trades += yr_trades

        year_results.append(YearResult(
            year=2015 + y,
            start_nav=year_daily_navs[0] if year_daily_navs else 0,
            end_nav=end_nav,
            deposits=yr_deposits, withdrawals=yr_withdrawals, dividends=yr_dividends,
            trades=yr_trades,
            avg_cash_pct=sum(cash_pcts) / len(cash_pcts) if cash_pcts else 0,
            max_drawdown=calc_max_drawdown(year_daily_navs),
        ))

    # Final sub-period return
    final_nav = daily_navs[-1] if daily_navs else initial_nav
    if nav_after_last_cf > 0:
        twr_sub_returns.append(final_nav / nav_after_last_cf - 1)

    # Compute metrics
    twr = calc_twr(twr_sub_returns, years)
    mwrr = calc_mwrr(initial_nav, final_nav, mwrr_cashflows, years)
    sharpe = calc_sharpe(daily_navs)
    max_dd = calc_max_drawdown(daily_navs)

    return ScenarioResult(
        name=name,
        initial_nav=initial_nav, final_nav=final_nav,
        total_deposits=total_deposits, total_withdrawals=total_withdrawals,
        total_dividends=total_dividends,
        total_trades=total_trades,
        twr_annual=twr,
        mwrr_annual=mwrr,
        avg_cash_drag=sum(yr.avg_cash_pct for yr in year_results) / len(year_results),
        max_drawdown=max_dd,
        sharpe=sharpe,
        years=year_results,
    )


# ── Formatting ─────────────────────────────────────────────────────

def fmt_vnd(v: float) -> str:
    return f"{v / 1e9:.2f}B"

def fmt_pct(v: float) -> str:
    return f"{v:.2f}%"


# ── Main ───────────────────────────────────────────────────────────

def run_backtest(seed: int = SEED) -> list[ScenarioResult]:
    rng = random.Random(seed)

    initial_nav = rng.uniform(1e9, 10e9)
    print(f"\n{'='*78}")
    print(f"  PE5Y CASH FLOW BACKTEST — mu={ANNUAL_MEAN_RETURN:.0%}, sigma={ANNUAL_VOLATILITY:.0%}")
    print(f"{'='*78}")
    print(f"  Initial NAV: {fmt_vnd(initial_nav)} | {NUM_STOCKS} stocks | {SIM_YEARS}y (2015-2024)")
    print(f"{'='*78}\n")

    total_days = SIM_YEARS * TRADING_DAYS_PER_YEAR
    all_prices = generate_stock_prices(NUM_STOCKS, total_days, rng=rng)

    # Generate identical cash events for all scenarios
    cash_events_by_year: list[list[CashEvent]] = []
    nav_est = initial_nav
    for y in range(SIM_YEARS):
        events = generate_cash_events(nav_est, rng)
        cash_events_by_year.append(events)
        nav_est *= (1 + ANNUAL_MEAN_RETURN)
        for ev in events:
            if ev.event_type == "deposit":
                nav_est += ev.amount
            elif ev.event_type == "withdraw":
                nav_est -= ev.amount

    # Cash events summary
    print("CASH EVENTS (identical for all scenarios):")
    print(f"{'Year':<6} {'Deposits':>12} {'Withdrawals':>14} {'Dividends':>12} {'#':>4}")
    print("-" * 52)
    for y, events in enumerate(cash_events_by_year):
        deps = sum(e.amount for e in events if e.event_type == "deposit")
        withs = sum(e.amount for e in events if e.event_type == "withdraw")
        divs = sum(e.amount for e in events if e.event_type == "dividend")
        print(f"{2015+y:<6} {fmt_vnd(deps):>12} {fmt_vnd(withs):>14} {fmt_vnd(divs):>12} {len(events):>4}")
    print()

    # Run all scenarios
    scenarios = [
        ("Baseline (no cash flows)", "none"),
        ("Cash-hold (idle til Sep)", "hold"),
        ("Pro-rata (deploy now)", "prorata"),
        ("Threshold (>5% NAV)", "threshold"),
    ]

    results: list[ScenarioResult] = []
    for name, mode in scenarios:
        print(f"Running: {name}...")
        r = simulate(name, initial_nav, all_prices, cash_events_by_year, SIM_YEARS, mode)
        results.append(r)

    # ── Results Table ──
    print(f"\n{'='*78}")
    print(f"  RESULTS — TWR = investment skill, MWRR = investor return")
    print(f"{'='*78}\n")

    header = (
        f"{'Scenario':<28} {'Final NAV':>10} {'TWR':>8} {'MWRR':>8} "
        f"{'Sharpe':>7} {'Cash%':>7} {'MaxDD':>7} {'Trades':>7}"
    )
    print(header)
    print("-" * len(header))

    for r in results:
        print(
            f"{r.name:<28} "
            f"{fmt_vnd(r.final_nav):>10} "
            f"{fmt_pct(r.twr_annual * 100):>8} "
            f"{fmt_pct(r.mwrr_annual * 100):>8} "
            f"{r.sharpe:>7.2f} "
            f"{fmt_pct(r.avg_cash_drag):>7} "
            f"{fmt_pct(r.max_drawdown):>7} "
            f"{r.total_trades:>7}"
        )

    # ── Cash Flow Impact ──
    print(f"\n{'='*78}")
    print(f"  CASH FLOW IMPACT")
    print(f"{'='*78}\n")

    for r in results[1:]:
        net = r.total_deposits - r.total_withdrawals + r.total_dividends
        print(f"  {r.name}: deposits {fmt_vnd(r.total_deposits)}, "
              f"withdrawals {fmt_vnd(r.total_withdrawals)}, "
              f"dividends {fmt_vnd(r.total_dividends)}, "
              f"net {fmt_vnd(net)}")
    print()

    # ── TWR Delta (key comparison) ──
    print(f"{'='*78}")
    print(f"  TWR COMPARISON (investment skill, cash-flow neutral)")
    print(f"{'='*78}\n")

    baseline_twr = results[0].twr_annual
    print(f"  Baseline TWR (pure return): {fmt_pct(baseline_twr * 100)}")
    print()
    for r in results[1:]:
        delta = (r.twr_annual - baseline_twr) * 100
        print(f"  {r.name:<28} TWR: {fmt_pct(r.twr_annual * 100):>8}  "
              f"delta vs baseline: {delta:+.2f}%")

    # ── Year-by-Year Pro-rata ──
    print(f"\n{'='*78}")
    print(f"  YEAR-BY-YEAR: PRO-RATA")
    print(f"{'='*78}\n")

    prorata = results[2]
    h2 = f"{'Year':<6} {'Start':>10} {'End':>10} {'Dep':>8} {'With':>8} {'Div':>8} {'Cash%':>7} {'DD':>7}"
    print(h2)
    print("-" * len(h2))
    for yr in prorata.years:
        print(
            f"{yr.year:<6} "
            f"{fmt_vnd(yr.start_nav):>10} "
            f"{fmt_vnd(yr.end_nav):>10} "
            f"{fmt_vnd(yr.deposits):>8} "
            f"{fmt_vnd(yr.withdrawals):>8} "
            f"{fmt_vnd(yr.dividends):>8} "
            f"{fmt_pct(yr.avg_cash_pct):>7} "
            f"{fmt_pct(yr.max_drawdown):>7}"
        )

    # ── Conclusion ──
    print(f"\n{'='*78}")
    print(f"  CONCLUSION")
    print(f"{'='*78}\n")

    best = max(results[1:], key=lambda r: r.twr_annual)
    worst = min(results[1:], key=lambda r: r.twr_annual)
    print(f"  Best TWR:  {best.name} ({fmt_pct(best.twr_annual * 100)})")
    print(f"  Worst TWR: {worst.name} ({fmt_pct(worst.twr_annual * 100)})")
    print(f"  TWR gap:   {fmt_pct((best.twr_annual - worst.twr_annual) * 100)}")
    print(f"\n  Pro-rata cash drag: {fmt_pct(prorata.avg_cash_drag)} vs "
          f"Cash-hold: {fmt_pct(results[1].avg_cash_drag)} vs "
          f"Threshold: {fmt_pct(results[3].avg_cash_drag)}")
    print()

    return results


if __name__ == "__main__":
    run_backtest()
