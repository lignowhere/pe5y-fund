"""Size the active strategy as if it was bought on its rebalance date."""
from __future__ import annotations

import math
import sqlite3
from types import SimpleNamespace
from typing import Any, Iterable

from ..config import AppConfig
from ..strategy.position_sizer import (
    query_adv_20d_historical,
    query_latest_price_date,
)
from .cycle import ActiveCycle, PlannerDataError, resolve_active_cycle
from .corporate_actions import CorporateActionError, build_verified_ledger
from .market_data import (
    first_prices_on_or_after,
    opens_on_date,
    prices_on_date,
    vendor_adjusted_price_pairs,
    verified_benchmark_total_return_pair,
)


def _uses_vendor_adjusted_mode(trust_tier: str) -> bool:
    return trust_tier in {"legacy_research", "trusted_local"}


def build_strategy_drift_targets(
    config: AppConfig,
    nav_vnd: float,
    cycle: ActiveCycle,
    *,
    extra_symbols: Iterable[str] = (),
    valuation_date: str | None = None,
    adjusted_prices: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Scale buy-date share ratios to today's NAV without re-equal-weighting."""
    symbols = cycle.symbols
    vendor_adjusted_mode = _uses_vendor_adjusted_mode(cycle.trust_tier)
    current_date = valuation_date or query_latest_price_date(config.db_path)
    if not current_date:
        raise PlannerDataError("Không xác định được ngày giá thị trường hiện tại")

    rebalance_prices = cycle.rebalance_prices or first_prices_on_or_after(
        config.db_path,
        symbols,
        cycle.execution_date or cycle.rebalance_date,
        config.strategy.max_rebalance_gap_days,
        config.strategy.close_scale_vnd,
    )
    all_symbols = sorted(set(symbols) | {s.upper() for s in extra_symbols if s})
    current_prices = prices_on_date(
        config.db_path,
        all_symbols,
        current_date,
        config.strategy.close_scale_vnd,
        require_verified=(
            cycle.snapshot_id is not None
            and cycle.trust_tier == "strict_pit"
        ),
        expected_source=cycle.canonical_price_source,
    )

    missing_rebalance = [
        symbol for symbol in symbols if symbol not in rebalance_prices
    ]
    missing_current = [
        symbol for symbol in all_symbols if symbol not in current_prices
    ]
    errors = []
    if missing_rebalance:
        errors.append(
            "thiếu giá ngày chiến lược: " + ", ".join(missing_rebalance)
        )
    if missing_current:
        errors.append(
            f"thiếu giá ngày {current_date}: " + ", ".join(missing_current)
        )
    if errors:
        raise PlannerDataError("; ".join(errors))

    benchmark_symbol = config.strategy.benchmark_symbol
    execution_date = cycle.execution_date or cycle.rebalance_date
    if _uses_vendor_adjusted_mode(cycle.trust_tier):
        benchmark_rebalance = opens_on_date(
            config.db_path,
            [benchmark_symbol],
            execution_date,
            1.0,
        ).get(benchmark_symbol)
    else:
        benchmark_rebalance = first_prices_on_or_after(
            config.db_path,
            [benchmark_symbol],
            execution_date,
            config.strategy.max_rebalance_gap_days,
            1.0,
        ).get(benchmark_symbol)
    benchmark_current = prices_on_date(
        config.db_path,
        [benchmark_symbol],
        current_date,
        1.0,
    ).get(benchmark_symbol)
    if not benchmark_rebalance or not benchmark_current:
        raise PlannerDataError(
            f"Thiếu dữ liệu {benchmark_symbol} đồng kỳ "
            f"{cycle.rebalance_date}–{current_date}"
        )
    benchmark_verified = (
        verified_benchmark_total_return_pair(
            config.db_path,
            benchmark_symbol,
            execution_date,
            current_date,
        )
        if cycle.trust_tier == "strict_pit"
        else None
    )
    benchmark_adjusted = (
        adjusted_prices.get(benchmark_symbol)
        if adjusted_prices is not None
        else None
    )
    research_adjusted: dict[str, dict[str, Any]] = {}
    performance_source_as_of: str | None = None
    if _uses_vendor_adjusted_mode(cycle.trust_tier):
        research_adjusted = vendor_adjusted_price_pairs(
            config.db_path,
            [*symbols, benchmark_symbol],
            execution_date,
            current_date,
        )
        missing_adjusted = [
            symbol
            for symbol in [*symbols, benchmark_symbol]
            if symbol not in research_adjusted
        ]
        if missing_adjusted:
            raise PlannerDataError(
                "Thiáº¿u chuá»—i giÃ¡ Ä‘iá»u chá»‰nh cÃ¹ng phiÃªn cho: "
                + ", ".join(missing_adjusted)
            )
        source_vintages = {
            str(item["source_as_of"])
            for item in research_adjusted.values()
        }
        if len(source_vintages) != 1:
            raise PlannerDataError(
                "CÃ¡c mÃ£ Ä‘ang dÃ¹ng khÃ¡c phiÃªn báº£n giÃ¡ Ä‘iá»u chá»‰nh"
            )
        performance_source_as_of = next(iter(source_vintages))
        benchmark_execution_close = prices_on_date(
            config.db_path,
            [benchmark_symbol],
            execution_date,
            1.0,
        ).get(benchmark_symbol)
        if not benchmark_execution_close:
            raise PlannerDataError(
                f"Thiáº¿u giÃ¡ Ä‘Ã³ng cá»­a {benchmark_symbol} ngÃ y {execution_date}"
            )
        benchmark_growth = (
            float(research_adjusted[benchmark_symbol]["end_value"])
            / float(research_adjusted[benchmark_symbol]["start_value"])
            * float(benchmark_execution_close["price_vnd"])
            / float(benchmark_rebalance["price_vnd"])
        )
    elif benchmark_verified:
        benchmark_growth = (
            float(benchmark_verified["end_value"])
            / float(benchmark_verified["start_value"])
        )
    elif benchmark_adjusted:
        benchmark_growth = (
            float(benchmark_adjusted["adjusted_current_price_vnd"])
            / float(
                benchmark_adjusted["adjusted_rebalance_price_vnd"]
            )
        )
    else:
        benchmark_growth = (
            float(benchmark_current["price_vnd"])
            / float(benchmark_rebalance["price_vnd"])
        )

    if cycle.adv_shares is not None:
        adv = cycle.adv_shares
    else:
        with sqlite3.connect(str(config.db_path)) as adv_conn:
            adv_conn.row_factory = sqlite3.Row
            adv = query_adv_20d_historical(
                adv_conn,
                symbols,
                cycle.execution_date or cycle.rebalance_date,
            )
    missing_adv = [symbol for symbol in symbols if adv.get(symbol, 0) <= 0]
    if missing_adv:
        raise PlannerDataError(
            "Thiếu ADV lịch sử trước ngày chiến lược: "
            + ", ".join(missing_adv)
        )

    count = len(symbols)
    initial_weight = 1.0 / count
    execution_date = cycle.execution_date or cycle.rebalance_date
    if _uses_vendor_adjusted_mode(cycle.trust_tier):
        ledger = {}
        execution_closes = prices_on_date(
            config.db_path,
            symbols,
            execution_date,
            config.strategy.close_scale_vnd,
        )
        missing_execution_close = [
            symbol for symbol in symbols if symbol not in execution_closes
        ]
        if missing_execution_close:
            raise PlannerDataError(
                "Missing execution-date closes: "
                + ", ".join(missing_execution_close)
            )
        for symbol in symbols:
            execution_open = float(
                rebalance_prices[symbol]["price_vnd"]
            )
            execution_close = float(
                execution_closes[symbol]["price_vnd"]
            )
            current_price = float(current_prices[symbol]["price_vnd"])
            pair = research_adjusted[symbol]
            total_return_growth = (
                float(pair["end_value"])
                / float(pair["start_value"])
                * execution_close
                / execution_open
            )
            ledger[symbol] = SimpleNamespace(
                share_factor=(
                    total_return_growth
                    * execution_open
                    / current_price
                ),
                cash_vnd_per_initial_share=0.0,
                event_count=0,
            )
        performance_basis = (
            "vendor_adjusted_total_return_user_confirmed"
            if cycle.trust_tier == "trusted_local"
            else "vendor_adjusted_total_return_research"
        )
    else:
        try:
            ledger = build_verified_ledger(
                config.db_path,
                symbols,
                execution_date,
                current_date,
            )
        except CorporateActionError as exc:
            raise PlannerDataError(str(exc)) from exc
        performance_basis = "verified_corporate_action_ledger_v1"
    stock_growth: dict[str, float] = {}
    total_growth: dict[str, float] = {}
    cash_growth: dict[str, float] = {}
    for symbol in symbols:
        execution_price = float(rebalance_prices[symbol]["price_vnd"])
        current_price = float(current_prices[symbol]["price_vnd"])
        if execution_price <= 0 or current_price <= 0:
            raise PlannerDataError(f"Giá không hợp lệ cho {symbol}")
        action = ledger[symbol]
        stock_growth[symbol] = (
            action.share_factor * current_price / execution_price
        )
        cash_growth[symbol] = (
            action.cash_vnd_per_initial_share / execution_price
        )
        total_growth[symbol] = (
            stock_growth[symbol] + cash_growth[symbol]
        )
    weighted_growth = sum(
        initial_weight * total_growth[symbol] for symbol in symbols
    )
    if weighted_growth <= 0:
        raise PlannerDataError("Không thể tính tỷ trọng trôi của danh mục")
    price_returns = {
        symbol: (total_growth[symbol] - 1.0) * 100.0
        for symbol in symbols
    }
    model_cash_weight = (
        sum(initial_weight * cash_growth[symbol] for symbol in symbols)
        / weighted_growth
    )

    lot_size = config.strategy.lot_size
    rows = []
    deployed = 0.0
    liquidity_limited_count = 0
    rank_map = {candidate.symbol: candidate.signal_rank for candidate in cycle.selected}
    for symbol in symbols:
        current_price = current_prices[symbol]["price_vnd"]
        drift_weight = (
            initial_weight * stock_growth[symbol] / weighted_growth
        )
        desired_value = nav_vnd * drift_weight
        desired_shares = (
            math.floor(desired_value / current_price / lot_size) * lot_size
        )
        shares_per_day = math.floor(
            adv[symbol] * config.strategy.participation_rate
        )
        capacity_shares = (
            math.floor(
                shares_per_day * config.strategy.accum_days / lot_size
            )
            * lot_size
        )
        target_shares = min(desired_shares, capacity_shares)
        liquidity_limited = target_shares < desired_shares
        if liquidity_limited:
            liquidity_limited_count += 1
        target_value = target_shares * current_price
        deployed += target_value
        rows.append({
            "symbol": symbol,
            "signal_rank": rank_map[symbol],
            "source": "PRIMARY",
            "rebalance_price_vnd": rebalance_prices[symbol]["price_vnd"],
            "rebalance_price_date": rebalance_prices[symbol]["price_date"],
            "current_price_vnd": current_price,
            "price_date": current_date,
            "adjusted_rebalance_price_vnd": (
                research_adjusted[symbol]["start_value"]
                if vendor_adjusted_mode
                else rebalance_prices[symbol]["price_vnd"]
            ),
            "corporate_action_share_factor": (
                None
                if vendor_adjusted_mode
                else ledger[symbol].share_factor
            ),
            "cash_dividend_vnd_per_initial_share": (
                None
                if vendor_adjusted_mode
                else ledger[symbol].cash_vnd_per_initial_share
            ),
            "corporate_action_count": (
                None
                if vendor_adjusted_mode
                else ledger[symbol].event_count
            ),
            "price_return_pct": round(price_returns[symbol], 4),
            "initial_weight_pct": round(initial_weight * 100, 4),
            "drift_weight_pct": round(drift_weight * 100, 4),
            "target_weight_pct": round(target_value / nav_vnd * 100, 4),
            "desired_shares": desired_shares,
            "target_shares": target_shares,
            "target_value_vnd": target_value,
            "adv_shares": adv[symbol],
            "capacity_shares": capacity_shares,
            "liquidity_limited": liquidity_limited,
        })

    return {
        "formation_year": cycle.formation_year,
        "hold_year": cycle.hold_year,
        "rebalance_date": cycle.rebalance_date,
        "signal_cutoff": cycle.signal_cutoff,
        "signal_price_date": cycle.signal_price_date,
        "execution_date": cycle.execution_date,
        "snapshot_id": cycle.snapshot_id,
        "snapshot_set_id": cycle.snapshot_set_id,
        "snapshot_created_at": cycle.snapshot_created_at,
        "financial_data_version_id": cycle.financial_data_version_id,
        "financial_content_hash": cycle.financial_content_hash,
        "methodology_version": cycle.methodology_version,
        "universe_count": cycle.universe_count,
        "price_date": current_date,
        "price_basis": "strategy_date_drift",
        "performance_basis": performance_basis,
        "trust_tier": cycle.trust_tier,
        "performance_source_as_of": performance_source_as_of,
        "model_growth_multiple": weighted_growth,
        "benchmark": {
            "symbol": benchmark_symbol,
            "rebalance_date": (
                benchmark_verified["start_date"]
                if benchmark_verified
                else execution_date
                if vendor_adjusted_mode
                else benchmark_adjusted["rebalance_price_date"]
                if benchmark_adjusted
                else benchmark_rebalance["price_date"]
            ),
            "rebalance_value": (
                benchmark_verified["start_value"]
                if benchmark_verified
                else benchmark_rebalance["price_vnd"]
                if vendor_adjusted_mode
                else benchmark_adjusted["adjusted_rebalance_price_vnd"]
                if benchmark_adjusted
                else benchmark_rebalance["price_vnd"]
            ),
            "current_date": (
                benchmark_verified["end_date"]
                if benchmark_verified
                else current_date
                if vendor_adjusted_mode
                else benchmark_adjusted["valuation_date"]
                if benchmark_adjusted
                else benchmark_current["price_date"]
            ),
            "current_value": (
                benchmark_verified["end_value"]
                if benchmark_verified
                else benchmark_current["price_vnd"]
                if vendor_adjusted_mode
                else benchmark_adjusted["adjusted_current_price_vnd"]
                if benchmark_adjusted
                else benchmark_current["price_vnd"]
            ),
            "growth_multiple": benchmark_growth,
            "performance_basis": (
                "verified_total_return_index"
                if benchmark_verified
                else "vendor_adjusted_total_return_user_confirmed"
                if cycle.trust_tier == "trusted_local"
                else "vendor_adjusted_total_return_research"
                if cycle.trust_tier == "legacy_research"
                else "vendor_adjusted_comparison"
                if benchmark_adjusted
                else "unadjusted_price_index"
            ),
            "authoritative": bool(benchmark_verified),
            "source_authority": (
                benchmark_verified["source_authority"]
                if benchmark_verified
                else research_adjusted[benchmark_symbol]["source"]
                if vendor_adjusted_mode
                else None
            ),
        },
        "positions": rows,
        "prices": current_prices,
        "summary": {
            "strategy_price_return_pct": round(
                (weighted_growth - 1.0) * 100.0, 4
            ),
            "model_value_per_100m_vnd": round(
                100_000_000.0 * weighted_growth
            ),
            "benchmark_symbol": benchmark_symbol,
            "benchmark_return_pct": round(
                (benchmark_growth - 1.0) * 100.0, 4
            ),
            "benchmark_value_per_100m_vnd": round(
                100_000_000.0 * benchmark_growth
            ),
            "excess_return_pct": round(
                (weighted_growth - benchmark_growth) * 100.0, 4
            ),
            "gainers_count": sum(
                value > 0 for value in price_returns.values()
            ),
            "losers_count": sum(
                value < 0 for value in price_returns.values()
            ),
            "unchanged_count": sum(
                value == 0 for value in price_returns.values()
            ),
            "target_stock_count": sum(
                1 for row in rows if row["target_shares"] > 0
            ),
            "target_deployed_vnd": deployed,
            "model_cash_weight_pct": round(model_cash_weight * 100.0, 4),
            "model_cash_vnd": round(nav_vnd * model_cash_weight),
            "target_cash_vnd": nav_vnd - deployed,
            "liquidity_limited_count": liquidity_limited_count,
        },
    }
