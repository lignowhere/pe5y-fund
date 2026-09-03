"""Compare TTM up-to-20Q with LAST-8Q+ over ten September cycles.

This is deliberately labelled a legacy research backtest: the pre-2018
financial archive has no verified publication timestamps. Signal formation
therefore uses the historical reporting-lag rule and the mutable legacy ratios.
Performance uses Vietcap's adjusted gap-chart series so corporate actions are
reflected in holding-period returns.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import logging
import math
import sqlite3
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..config import AppConfig, get_config
from ..data.updater import _ts_to_date
from ..data.vci_client import VCIClient
from ..fund.market_data import first_prices_on_or_after
from ..strategy.position_sizer import query_adv_20d_historical
from ..strategy.signal_pe_ttm_20q import (
    PE20QCandidate,
    generate_signal_20q,
    select_top_n_20q,
)

log = logging.getLogger(__name__)
HOLD_YEARS = tuple(range(2015, 2025))
STRATEGIES = {
    "TTM_20Q": 0,
    "LAST_8Q_PLUS": 8,
}


def quarter_count_for_cycle(hold_year: int) -> int:
    """Use all archive history available to the early September cycles."""
    return min(20, max(8, 8 + 4 * (hold_year - HOLD_YEARS[0])))


def _first_price(
    prices: dict[str, float], target: str, max_gap_days: int
) -> tuple[str, float] | None:
    start = dt.date.fromisoformat(target)
    for offset in range(max_gap_days + 1):
        date = (start + dt.timedelta(days=offset)).isoformat()
        price = prices.get(date)
        if price is not None and price > 0:
            return date, price
    return None


def _fetch_adjusted_histories(
    config: AppConfig,
    symbols: list[str],
    *,
    count_back: int,
    workers: int,
) -> dict[str, dict[str, float]]:
    client = VCIClient(config.vci.rate_limit_rpm)
    histories: dict[str, dict[str, float]] = {}
    failures: list[str] = []

    def fetch(symbol: str) -> tuple[str, dict[str, float]]:
        bars = client.get_ohlcv(symbol, count_back=count_back)
        history = {
            date: float(bar["close"])
            for bar in bars
            if (date := _ts_to_date(bar.get("time")))
            and bar.get("close") is not None
            and float(bar["close"]) > 0
        }
        if not history:
            raise RuntimeError("no adjusted bars returned")
        return symbol, history

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fetch, symbol): symbol for symbol in symbols}
            completed = 0
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    key, value = future.result()
                    histories[key] = value
                except Exception as exc:
                    failures.append(f"{symbol}: {exc}")
                completed += 1
                if completed % 25 == 0 or completed == len(symbols):
                    log.info(
                        "Adjusted prices: %d/%d symbols",
                        completed,
                        len(symbols),
                    )
    finally:
        client.close()
    if failures:
        raise RuntimeError(
            "Adjusted-price download incomplete: " + "; ".join(failures[:20])
        )
    return histories


def _build_signals(
    config: AppConfig,
    select_pct: float,
) -> dict[str, list[dict[str, Any]]]:
    results = {strategy: [] for strategy in STRATEGIES}
    for hold_year in HOLD_YEARS:
        quarter_count = quarter_count_for_cycle(hold_year)
        rebalance_date = (
            f"{hold_year}-{config.strategy.rebalance_month:02d}-01"
        )
        for strategy, last_positive in STRATEGIES.items():
            candidates = generate_signal_20q(
                config.db_path,
                hold_year - 1,
                config,
                hold_year=hold_year,
                rebalance_date=rebalance_date,
                rebalance_month=config.strategy.rebalance_month,
                quarter_count=quarter_count,
                require_all_positive=False,
                require_last_n_positive=last_positive,
            )
            selected = select_top_n_20q(
                candidates,
                select_pct,
                min_holdings=config.strategy.min_holdings,
            )
            if len(candidates) >= config.strategy.min_holdings:
                if len(selected) < config.strategy.min_holdings:
                    raise RuntimeError(
                        f"{strategy} {hold_year}: min-holdings invariant failed"
                    )
            results[strategy].append(
                {
                    "hold_year": hold_year,
                    "period": f"{hold_year}-{hold_year + 1}",
                    "formation_year": hold_year - 1,
                    "quarter_count": quarter_count,
                    "rebalance_date": rebalance_date,
                    "sell_date": (
                        f"{hold_year + 1}-"
                        f"{config.strategy.rebalance_month:02d}-01"
                    ),
                    "universe_count": len(candidates),
                    "selected": selected,
                }
            )
            log.info(
                "%s %d: %dQ, universe=%d, selected=%d",
                strategy,
                hold_year,
                quarter_count,
                len(candidates),
                len(selected),
            )
    return results


def _simulate_cycle(
    config: AppConfig,
    cycle: dict[str, Any],
    adjusted: dict[str, dict[str, float]],
    capital_vnd: float,
) -> dict[str, Any]:
    selected: list[PE20QCandidate] = cycle["selected"]
    symbols = [candidate.symbol for candidate in selected]
    raw_buy = first_prices_on_or_after(
        config.db_path,
        symbols,
        cycle["rebalance_date"],
        config.strategy.max_rebalance_gap_days,
        config.strategy.close_scale_vnd,
    )
    raw_sell = first_prices_on_or_after(
        config.db_path,
        symbols,
        cycle["sell_date"],
        config.strategy.max_rebalance_gap_days,
        config.strategy.close_scale_vnd,
    )
    missing_raw = [
        symbol
        for symbol in symbols
        if symbol not in raw_buy or symbol not in raw_sell
    ]
    if missing_raw:
        raise RuntimeError(
            f"{cycle['period']}: missing raw prices: "
            + ", ".join(missing_raw)
        )
    with sqlite3.connect(str(config.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        adv = query_adv_20d_historical(
            conn, symbols, cycle["rebalance_date"]
        )
    missing_adv = [symbol for symbol in symbols if symbol not in adv]
    if missing_adv:
        raise RuntimeError(
            f"{cycle['period']}: missing ADV: " + ", ".join(missing_adv)
        )

    lot = config.strategy.lot_size
    buy_fee = config.strategy.broker_fee_bps / 10_000.0
    sell_fee_tax = (
        config.strategy.broker_fee_bps + config.strategy.sell_tax_bps
    ) / 10_000.0
    per_stock = capital_vnd / len(symbols)
    positions: list[dict[str, Any]] = []
    total_buy_cost = 0.0
    total_sell_value = 0.0
    for candidate in selected:
        symbol = candidate.symbol
        raw_buy_price = float(raw_buy[symbol]["price_vnd"])
        desired = (
            math.floor(
                per_stock / (raw_buy_price * (1 + buy_fee)) / lot
            )
            * lot
        )
        capacity = (
            math.floor(
                adv[symbol]
                * config.strategy.participation_rate
                * config.strategy.accum_days
                / lot
            )
            * lot
        )
        shares = min(desired, capacity)
        if shares <= 0:
            raise RuntimeError(f"{cycle['period']}: zero shares for {symbol}")
        adj_buy = _first_price(
            adjusted[symbol],
            cycle["rebalance_date"],
            config.strategy.max_rebalance_gap_days,
        )
        adj_sell = _first_price(
            adjusted[symbol],
            cycle["sell_date"],
            config.strategy.max_rebalance_gap_days,
        )
        if adj_buy is None or adj_sell is None:
            raise RuntimeError(
                f"{cycle['period']}: missing adjusted prices for {symbol}"
            )
        buy_market_value = shares * raw_buy_price
        buy_cost = buy_market_value * (1 + buy_fee)
        gross_factor = adj_sell[1] / adj_buy[1]
        sell_value = buy_market_value * gross_factor * (1 - sell_fee_tax)
        total_buy_cost += buy_cost
        total_sell_value += sell_value
        positions.append(
            {
                "symbol": symbol,
                "rank": candidate.signal_rank,
                "quarters_count": candidate.quarters_count,
                "avg_eps": candidate.avg_eps_20q,
                "pe": candidate.pe_ttm_20q,
                "market_cap_vnd": candidate.market_cap_vnd,
                "raw_buy_date": raw_buy[symbol]["price_date"],
                "raw_buy_price_vnd": raw_buy_price,
                "raw_sell_date": raw_sell[symbol]["price_date"],
                "raw_sell_price_vnd": raw_sell[symbol]["price_vnd"],
                "adjusted_buy_date": adj_buy[0],
                "adjusted_buy_price_vnd": adj_buy[1],
                "adjusted_sell_date": adj_sell[0],
                "adjusted_sell_price_vnd": adj_sell[1],
                "adjusted_gross_return": gross_factor - 1,
                "adv_20d_shares": adv[symbol],
                "desired_shares": desired,
                "capacity_shares": capacity,
                "shares": shares,
                "buy_cost_vnd": buy_cost,
                "ending_value_vnd": sell_value,
                "net_position_return": sell_value / buy_cost - 1,
            }
        )
    cash = capital_vnd - total_buy_cost
    ending_value = cash + total_sell_value
    return {
        **{k: v for k, v in cycle.items() if k != "selected"},
        "selected_count": len(symbols),
        "position_count": len(positions),
        "cash_vnd": cash,
        "cash_drag_pct": cash / capital_vnd * 100,
        "ending_value_vnd": ending_value,
        "return": ending_value / capital_vnd - 1,
        "positions": positions,
    }


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [float(row["return"]) for row in rows]
    compounded = math.prod(1 + value for value in returns)
    return {
        "cycles": len(returns),
        "cagr": compounded ** (1 / len(returns)) - 1,
        "total_return": compounded - 1,
        "positive_cycles": sum(value > 0 for value in returns),
        "win_rate": sum(value > 0 for value in returns) / len(returns),
        "best_return": max(returns),
        "worst_return": min(returns),
        "annual_volatility": statistics.pstdev(returns),
        "average_holdings": statistics.mean(
            row["selected_count"] for row in rows
        ),
        "average_cash_drag_pct": statistics.mean(
            row["cash_drag_pct"] for row in rows
        ),
    }


def _active_financial_version(db_path: Path) -> dict[str, Any] | None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM financial_data_versions WHERE is_active = 1"
        ).fetchone()
    return dict(row) if row else None


def _write_outputs(result: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{digest}  {output.name}\n", encoding="utf-8"
    )

    annual_path = output.with_name(output.stem + "-annual.csv")
    with annual_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(result["annual_comparison"][0]),
        )
        writer.writeheader()
        writer.writerows(result["annual_comparison"])

    position_path = output.with_name(output.stem + "-positions.csv")
    fields = [
        "strategy", "period", "quarter_count", "symbol", "rank",
        "pe", "avg_eps", "quarters_count", "raw_buy_date",
        "raw_buy_price_vnd", "adjusted_buy_price_vnd", "adjusted_sell_date",
        "adjusted_sell_price_vnd", "adjusted_gross_return",
        "adv_20d_shares", "shares", "net_position_return",
    ]
    with position_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for strategy, payload in result["strategies"].items():
            for cycle in payload["cycles"]:
                for position in cycle["positions"]:
                    writer.writerow(
                        {
                            "strategy": strategy,
                            "period": cycle["period"],
                            "quarter_count": cycle["quarter_count"],
                            **{
                                field: position.get(field)
                                for field in fields[3:]
                            },
                        }
                    )


def run(
    config: AppConfig,
    *,
    select_pct: float,
    capital_vnd: float,
    count_back: int,
    workers: int,
) -> dict[str, Any]:
    signals = _build_signals(config, select_pct)
    symbols = sorted(
        {
            candidate.symbol
            for cycles in signals.values()
            for cycle in cycles
            for candidate in cycle["selected"]
        }
        | {config.strategy.benchmark_symbol}
    )
    log.info("Fetching adjusted histories for %d unique symbols", len(symbols))
    adjusted = _fetch_adjusted_histories(
        config, symbols, count_back=count_back, workers=workers
    )

    strategy_results: dict[str, dict[str, Any]] = {}
    for strategy, cycles in signals.items():
        simulated = [
            _simulate_cycle(config, cycle, adjusted, capital_vnd)
            for cycle in cycles
        ]
        strategy_results[strategy] = {
            "metrics": _metrics(simulated),
            "cycles": simulated,
        }

    benchmark_rows = []
    benchmark_history = adjusted[config.strategy.benchmark_symbol]
    for hold_year in HOLD_YEARS:
        buy_date = f"{hold_year}-{config.strategy.rebalance_month:02d}-01"
        sell_date = (
            f"{hold_year + 1}-{config.strategy.rebalance_month:02d}-01"
        )
        buy = _first_price(
            benchmark_history,
            buy_date,
            config.strategy.max_rebalance_gap_days,
        )
        sell = _first_price(
            benchmark_history,
            sell_date,
            config.strategy.max_rebalance_gap_days,
        )
        if buy is None or sell is None:
            raise RuntimeError(f"Missing adjusted VNINDEX for {hold_year}")
        benchmark_rows.append(
            {
                "hold_year": hold_year,
                "period": f"{hold_year}-{hold_year + 1}",
                "buy_date": buy[0],
                "buy_price": buy[1],
                "sell_date": sell[0],
                "sell_price": sell[1],
                "return": sell[1] / buy[1] - 1,
            }
        )
    benchmark_metrics = _metrics(
        [
            {
                **row,
                "selected_count": 1,
                "cash_drag_pct": 0.0,
            }
            for row in benchmark_rows
        ]
    )

    annual = []
    for index, hold_year in enumerate(HOLD_YEARS):
        ttm = strategy_results["TTM_20Q"]["cycles"][index]
        last8 = strategy_results["LAST_8Q_PLUS"]["cycles"][index]
        benchmark = benchmark_rows[index]
        ttm_symbols = {row["symbol"] for row in ttm["positions"]}
        last8_symbols = {row["symbol"] for row in last8["positions"]}
        overlap = ttm_symbols & last8_symbols
        union = ttm_symbols | last8_symbols
        annual.append(
            {
                "period": f"{hold_year}-{hold_year + 1}",
                "quarter_count": ttm["quarter_count"],
                "ttm20_return_pct": ttm["return"] * 100,
                "last8q_return_pct": last8["return"] * 100,
                "vnindex_return_pct": benchmark["return"] * 100,
                "last8_minus_ttm_pp": (
                    last8["return"] - ttm["return"]
                ) * 100,
                "ttm20_excess_vs_vn_pp": (
                    ttm["return"] - benchmark["return"]
                ) * 100,
                "last8_excess_vs_vn_pp": (
                    last8["return"] - benchmark["return"]
                ) * 100,
                "ttm20_holdings": ttm["selected_count"],
                "last8q_holdings": last8["selected_count"],
                "overlap_count": len(overlap),
                "jaccard_pct": len(overlap) / len(union) * 100,
            }
        )

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "methodology": {
            "name": "legacy_mutable_reporting_lag_dynamic_quarters_v1",
            "point_in_time_status": "research_only_not_strict_pre_2018",
            "cycles": "September 2015-September 2025",
            "quarter_schedule": {
                "2015": 8,
                "2016": 12,
                "2017": 16,
                "2018-2024": 20,
            },
            "selection": (
                f"top {select_pct:g}% lowest PE; minimum "
                f"{config.strategy.min_holdings} holdings"
            ),
            "ttm20_filter": "average trailing EPS > 0",
            "last8q_filter": (
                "same trailing average plus latest 8 quarters EPS > 0"
            ),
            "performance_price_source": (
                "Vietcap VCI GAP_CHART adjusted close"
            ),
            "execution": (
                "equal target capital, lot rounding, historical ADV cap, "
                "broker fees and sell tax; unused capital remains cash"
            ),
        },
        "parameters": {
            "select_pct": select_pct,
            "min_holdings": config.strategy.min_holdings,
            "capital_vnd": capital_vnd,
            "rebalance_month": config.strategy.rebalance_month,
            "lot_size": config.strategy.lot_size,
            "participation_rate": config.strategy.participation_rate,
            "accum_days": config.strategy.accum_days,
            "broker_fee_bps_per_side": config.strategy.broker_fee_bps,
            "sell_tax_bps": config.strategy.sell_tax_bps,
            "adjusted_count_back": count_back,
        },
        "source_fingerprint": {
            "database": config.db_path.name,
            "database_size_bytes": config.db_path.stat().st_size,
            "database_modified_at": dt.datetime.fromtimestamp(
                config.db_path.stat().st_mtime, tz=dt.timezone.utc
            ).isoformat(),
            "active_financial_version": _active_financial_version(
                config.db_path
            ),
        },
        "strategies": strategy_results,
        "benchmark": {
            "symbol": config.strategy.benchmark_symbol,
            "metrics": benchmark_metrics,
            "cycles": benchmark_rows,
        },
        "annual_comparison": annual,
        "head_to_head": {
            "last8_wins": sum(
                row["last8q_return_pct"] > row["ttm20_return_pct"]
                for row in annual
            ),
            "ttm20_wins": sum(
                row["ttm20_return_pct"] > row["last8q_return_pct"]
                for row in annual
            ),
            "ties": sum(
                row["ttm20_return_pct"] == row["last8q_return_pct"]
                for row in annual
            ),
            "average_last8_minus_ttm_pp": statistics.mean(
                row["last8_minus_ttm_pp"] for row in annual
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/ttm20-vs-last8-10y-min15.json"),
    )
    parser.add_argument("--select-pct", type=float, default=10.0)
    parser.add_argument("--capital-vnd", type=float, default=5_000_000_000.0)
    parser.add_argument("--count-back", type=int, default=3500)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    result = run(
        get_config(),
        select_pct=args.select_pct,
        capital_vnd=args.capital_vnd,
        count_back=args.count_back,
        workers=args.workers,
    )
    _write_outputs(result, args.output)
    print(json.dumps(
        {
            "output": str(args.output),
            "ttm20": result["strategies"]["TTM_20Q"]["metrics"],
            "last8q": result["strategies"]["LAST_8Q_PLUS"]["metrics"],
            "vnindex": result["benchmark"]["metrics"],
            "head_to_head": result["head_to_head"],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
