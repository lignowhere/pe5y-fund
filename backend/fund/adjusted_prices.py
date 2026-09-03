"""Adjusted-price cache for live strategy total-return calculations."""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable, TYPE_CHECKING

from ..config import AppConfig
from ..data.updater import _ts_to_date
from ..data.vci_client import VCIClient
from ..database.connection import connect, connect_rw, fetch_all
from .market_data import first_prices_on_or_after

if TYPE_CHECKING:
    from .cycle import ActiveCycle


class AdjustedPriceError(RuntimeError):
    """Raised when a complete adjusted-price set cannot be produced."""


def ensure_adjusted_performance_prices(
    config: AppConfig,
    cycle: ActiveCycle,
    valuation_date: str,
    *,
    client: VCIClient | None = None,
    max_workers: int = 3,
    extra_symbols: Iterable[str] = (),
) -> dict[str, dict[str, Any]]:
    """Return one current adjusted-price pair for every cycle constituent."""
    if cycle.snapshot_id is None:
        raise AdjustedPriceError("Chu kỳ chưa có snapshot để gắn giá điều chỉnh")

    symbols = sorted(
        set(cycle.symbols)
        | {symbol.upper() for symbol in extra_symbols if symbol}
    )
    cached = _load_cache(
        config, cycle.snapshot_id, symbols, valuation_date
    )
    missing = [symbol for symbol in symbols if symbol not in cached]
    if not missing:
        return cached

    rebalance_prices = dict(cycle.rebalance_prices or {})
    extra_missing = [
        symbol for symbol in symbols if symbol not in rebalance_prices
    ]
    if extra_missing:
        execution_start = cycle.execution_date or cycle.rebalance_date
        rebalance_prices.update(
            first_prices_on_or_after(
                config.db_path,
                extra_missing,
                execution_start,
                config.strategy.max_rebalance_gap_days,
                (
                    1.0
                    if set(extra_missing)
                    == {config.strategy.benchmark_symbol}
                    else config.strategy.close_scale_vnd
                ),
            )
        )
    absent_dates = [
        symbol for symbol in missing
        if not (rebalance_prices.get(symbol) or {}).get("price_date")
    ]
    if absent_dates:
        raise AdjustedPriceError(
            "Thiếu ngày giá chiến lược cho: " + ", ".join(absent_dates)
        )

    count_back = _required_count_back(
        cycle.execution_date or cycle.rebalance_date,
        valuation_date,
    )
    own_client = client is None
    vci = client or VCIClient(config.vci.rate_limit_rpm)
    fetched: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    def fetch_symbol(symbol: str) -> tuple[str, dict[str, Any]]:
        bars = vci.get_ohlcv(symbol, count_back=count_back)
        prices = {
            date: float(bar["close"])
            for bar in bars
            if (date := _ts_to_date(bar.get("time")))
            and bar.get("close") is not None
        }
        rebalance_price_date = rebalance_prices[symbol]["price_date"]
        rebalance_price = prices.get(rebalance_price_date)
        current_price = prices.get(valuation_date)
        if not rebalance_price or not current_price:
            missing_dates = [
                date for date, value in (
                    (rebalance_price_date, rebalance_price),
                    (valuation_date, current_price),
                )
                if not value
            ]
            raise AdjustedPriceError(
                f"{symbol} thiếu giá điều chỉnh ngày "
                + ", ".join(missing_dates)
            )
        return symbol, {
            "rebalance_price_date": rebalance_price_date,
            "adjusted_rebalance_price_vnd": rebalance_price,
            "valuation_date": valuation_date,
            "adjusted_current_price_vnd": current_price,
            "source": "VCI_GAP_CHART",
        }

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(fetch_symbol, symbol): symbol
                for symbol in missing
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    key, row = future.result()
                    fetched[key] = row
                except Exception as exc:
                    errors.append(f"{symbol}: {exc}")
    finally:
        if own_client:
            vci.close()

    if fetched:
        _store_cache(config, cycle.snapshot_id, fetched)
        cached.update(fetched)
    if errors or len(cached) != len(symbols):
        details = "; ".join(errors[:8])
        raise AdjustedPriceError(
            "Không thể cập nhật đủ giá điều chỉnh Vietcap"
            + (f": {details}" if details else "")
        )
    return cached


def _required_count_back(start_date: str, end_date: str) -> int:
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    calendar_days = max(1, (end - start).days)
    return min(2_000, max(60, calendar_days * 5 // 7 + 45))


def _load_cache(
    config: AppConfig,
    cycle_snapshot_id: int,
    symbols: list[str],
    valuation_date: str,
) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}
    with connect(config.db_path) as conn:
        rows = fetch_all(
            conn,
            f"""SELECT * FROM strategy_adjusted_price_cache
                WHERE cycle_snapshot_id = ? AND valuation_date = ?
                  AND symbol IN ({','.join('?' for _ in symbols)})""",
            (cycle_snapshot_id, valuation_date, *symbols),
        )
    return {row["symbol"]: row for row in rows}


def _store_cache(
    config: AppConfig,
    cycle_snapshot_id: int,
    rows: dict[str, dict[str, Any]],
) -> None:
    with connect_rw(config.db_path) as conn:
        conn.executemany(
            """INSERT INTO strategy_adjusted_price_cache
               (cycle_snapshot_id, symbol, rebalance_price_date,
                adjusted_rebalance_price_vnd, valuation_date,
                adjusted_current_price_vnd, source, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(cycle_snapshot_id, symbol) DO UPDATE SET
                 rebalance_price_date = excluded.rebalance_price_date,
                 adjusted_rebalance_price_vnd =
                   excluded.adjusted_rebalance_price_vnd,
                 valuation_date = excluded.valuation_date,
                 adjusted_current_price_vnd =
                   excluded.adjusted_current_price_vnd,
                 source = excluded.source,
                 fetched_at = CURRENT_TIMESTAMP""",
            [
                (
                    cycle_snapshot_id,
                    symbol,
                    row["rebalance_price_date"],
                    row["adjusted_rebalance_price_vnd"],
                    row["valuation_date"],
                    row["adjusted_current_price_vnd"],
                    row["source"],
                )
                for symbol, row in rows.items()
            ],
        )
