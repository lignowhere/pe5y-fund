"""Daily data update orchestrator — fetches missing prices and financials."""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from ..database.connection import connect, connect_rw, fetch_all, fetch_one
from .vci_client import VCIClient
from .kbs_client import KBSClient

log = logging.getLogger(__name__)

# Re-export financial updater for convenience
from .financial_updater import FinancialProgress, update_financials_stream  # noqa: E402, F401

# Date format expected in stock_price_history.time
_DATE_RE = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}$")



def _ts_to_date(ts) -> str | None:
    """Convert a timestamp (int, float, or string) to YYYY-MM-DD.

    Handles VCI returning unix timestamps as strings (e.g. '1771891200').
    Returns None for unparseable values.
    """
    if ts is None:
        return None
    # Already a proper date string?
    if isinstance(ts, str) and _DATE_RE.match(ts):
        return ts
    # Numeric or numeric-string → unix timestamp
    try:
        numeric = float(ts)
        return datetime.datetime.fromtimestamp(
            numeric, tz=datetime.timezone.utc
        ).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


@dataclass
class UpdateResult:
    symbols_updated: int
    symbols_failed: int
    prices_inserted: int
    errors: list[str]


def detect_missing_prices(
    db_path: Path,
    min_trading_day_gap: int = 3,
    max_stale_market_days: int = 30,
) -> list[str]:
    """Find actively-traded symbols that are behind the market by trading days.

    Uses actual market trading days (dates with >=50 symbols) to measure the
    gap, so weekends / holidays don't inflate the count.  A symbol is flagged
    only if it's >= *min_trading_day_gap* market days behind the latest.

    Symbols that have been stale for more than *max_stale_market_days* market
    days are excluded — they are likely suspended or delisted and cannot be
    updated from any data source.

    Default gap=3 means: if the market has traded 3+ days since a symbol's
    last data, it's considered missing.
    """
    with connect(db_path) as conn:
        # Fetch enough market days for both the gap check and staleness limit
        limit = max(max_stale_market_days + 1, 10)
        market_days = fetch_all(
            conn,
            """SELECT time FROM stock_price_history
               GROUP BY time HAVING COUNT(DISTINCT symbol) >= 50
               ORDER BY time DESC LIMIT ?""",
            (limit,),
        )
        if len(market_days) < min_trading_day_gap + 1:
            return []

        latest_date = market_days[0]["time"]
        # The Nth most recent market day — symbols behind this are truly stale
        gap_threshold = market_days[min_trading_day_gap]["time"]

        # Staleness cutoff: symbols older than this are likely suspended
        stale_cutoff = None
        if len(market_days) > max_stale_market_days:
            stale_cutoff = market_days[max_stale_market_days]["time"]

        rows = fetch_all(
            conn,
            """
            SELECT s.ticker
            FROM stocks s
            JOIN stock_exchange se ON se.ticker = s.ticker
            LEFT JOIN (
                SELECT symbol, MAX(time) AS latest
                FROM stock_price_history
                WHERE typeof(time) = 'text'
                  AND time GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                GROUP BY symbol
            ) sub ON sub.symbol = s.ticker
            WHERE LENGTH(s.ticker) = 3
              AND s.ticker GLOB '[A-Z][A-Z][A-Z]'
              AND se.exchange IN ('HSX', 'HNX', 'UPCOM')
              AND (
                  sub.latest IS NULL
                  OR (sub.latest < ? AND (? IS NULL OR sub.latest >= ?))
              )
            ORDER BY s.ticker
            """,
            (gap_threshold, stale_cutoff, stale_cutoff),
        )
    return [r["ticker"] for r in rows]


def detect_missing_financials(
    db_path: Path, year: Optional[int] = None
) -> list[dict[str, Any]]:
    """Find symbols missing financial ratio data for given year."""
    if year is None:
        year = datetime.date.today().year - 1
    with connect(db_path) as conn:
        rows = fetch_all(
            conn,
            """
            SELECT s.ticker
            FROM stocks s
            JOIN stock_exchange se ON se.ticker = s.ticker
            WHERE LENGTH(s.ticker) = 3
              AND s.ticker GLOB '[A-Z][A-Z][A-Z]'
              AND se.exchange IN ('HSX', 'HNX')
              AND s.ticker NOT IN (
                SELECT DISTINCT symbol FROM financial_ratios
                WHERE year = ? AND quarter IS NULL
              )
            ORDER BY s.ticker
            """,
            (year,),
        )
    return [{"ticker": r["ticker"], "year": year} for r in rows]


def update_prices(
    db_path: Path,
    symbols: list[str],
    vci: VCIClient,
    count_back: int = 30,
) -> UpdateResult:
    """Fetch and insert recent OHLCV data for given symbols."""
    updated, failed, inserted = 0, 0, 0
    errors: list[str] = []

    for sym in symbols:
        try:
            bars = vci.get_ohlcv(sym, count_back=count_back)
            if not bars:
                continue
            with connect_rw(db_path) as conn:
                sym_inserted = 0
                for bar in bars:
                    ts = bar.get("time")
                    if ts is None:
                        continue
                    # Convert unix timestamp to date string
                    date_str = _ts_to_date(ts)
                    if date_str is None:
                        continue
                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO stock_price_history
                        (symbol, time, open, high, low, close, volume)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (sym, date_str, bar["open"], bar["high"],
                         bar["low"], bar["close"], bar["volume"]),
                    )
                    sym_inserted += cur.rowcount
            inserted += sym_inserted
            if sym_inserted > 0:
                updated += 1
        except Exception as e:
            failed += 1
            errors.append(f"{sym}: {e}")
            log.warning("Failed to update prices for %s: %s", sym, e)

    return UpdateResult(updated, failed, inserted, errors)


@dataclass
class SymbolProgress:
    """Progress event emitted per symbol during streaming update."""
    symbol: str
    index: int
    total: int
    status: str  # "ok", "skip", "error"
    bars_inserted: int
    error: str | None
    skip_reason: str | None
    # running totals
    updated_so_far: int
    failed_so_far: int
    inserted_so_far: int


def _do_insert_bars(conn, sym: str, bars: list[dict]) -> int:
    """Insert OHLCV bars using an existing connection. Returns rows inserted."""
    count = 0
    for bar in bars:
        ts = bar.get("time")
        if ts is None:
            continue
        date_str = _ts_to_date(ts)
        if date_str is None:
            continue
        cur = conn.execute(
            """INSERT OR IGNORE INTO stock_price_history
            (symbol, time, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sym, date_str, bar["open"], bar["high"],
             bar["low"], bar["close"], bar["volume"]),
        )
        count += cur.rowcount
    return count


def _insert_bars(db_path: Path, sym: str, bars: list[dict], conn=None) -> int:
    """Insert OHLCV bars into DB. Returns count of newly inserted rows.

    If conn is provided, uses it directly (no new connection opened).
    """
    if conn is not None:
        return _do_insert_bars(conn, sym, bars)
    with connect_rw(db_path) as c:
        return _do_insert_bars(c, sym, bars)


def _latest_bar_date(bars: list[dict]) -> str | None:
    """Extract the latest date string from a list of bars."""
    dates = []
    for bar in bars:
        ts = bar.get("time")
        if ts is None:
            continue
        d = _ts_to_date(ts)
        if d:
            dates.append(d)
    return max(dates) if dates else None


def update_prices_stream(
    db_path: Path,
    symbols: list[str],
    vci: VCIClient,
    kbs: Optional[KBSClient] = None,
    count_back: int = 30,
) -> Iterator[SymbolProgress]:
    """Like update_prices but yields progress per symbol for SSE streaming.

    Falls back to KBS when VCI returns 0 bars OR when VCI returns only
    stale bars (all already in DB). This handles suspended stocks that VCI
    has old data for but KBS might have newer data.
    """
    updated, failed, inserted = 0, 0, 0
    total = len(symbols)

    with connect_rw(db_path) as conn:
      for idx, sym in enumerate(symbols):
        sym_bars = 0
        skip_reason = None
        source = "VCI"
        try:
            bars = vci.get_ohlcv(sym, count_back=count_back)

            # Phase 1: try VCI
            if bars:
                sym_bars = _insert_bars(db_path, sym, bars, conn=conn)
                if sym_bars == 0 and kbs is not None:
                    # VCI returned bars but all already existed → try KBS
                    vci_latest = _latest_bar_date(bars)
                    try:
                        kbs_bars = kbs.get_ohlcv(sym, count_back=count_back)
                        if kbs_bars:
                            kbs_latest = _latest_bar_date(kbs_bars)
                            # Only use KBS if it has newer data
                            if kbs_latest and (not vci_latest or kbs_latest > vci_latest):
                                sym_bars = _insert_bars(db_path, sym, kbs_bars, conn=conn)
                                source = "KBS"
                    except Exception:
                        pass  # KBS fallback failed silently
            elif kbs is not None:
                # VCI returned 0 bars → try KBS directly
                try:
                    bars = kbs.get_ohlcv(sym, count_back=count_back)
                    if bars:
                        sym_bars = _insert_bars(db_path, sym, bars, conn=conn)
                        source = "KBS"
                except Exception:
                    bars = []

            # Phase 2: determine status
            if not bars and sym_bars == 0:
                skip_reason = (
                    "both VCI and KBS returned 0 bars" if kbs
                    else "VCI returned 0 bars"
                )
                yield SymbolProgress(
                    symbol=sym, index=idx, total=total, status="skip",
                    bars_inserted=0, error=None, skip_reason=skip_reason,
                    updated_so_far=updated, failed_so_far=failed,
                    inserted_so_far=inserted,
                )
                continue

            inserted += sym_bars
            if sym_bars > 0:
                updated += 1
                status = "ok"
            else:
                status = "skip"
                latest = _latest_bar_date(bars) if bars else "?"
                skip_reason = (
                    f"no new data from {source} (latest: {latest})"
                )

            yield SymbolProgress(
                symbol=sym, index=idx, total=total, status=status,
                bars_inserted=sym_bars, error=None, skip_reason=skip_reason,
                updated_so_far=updated, failed_so_far=failed,
                inserted_so_far=inserted,
            )
        except Exception as e:
            failed += 1
            err_msg = f"{sym}: {e}"
            log.warning("Failed to update prices for %s: %s", sym, e)
            yield SymbolProgress(
                symbol=sym, index=idx, total=total, status="error",
                bars_inserted=0, error=err_msg, skip_reason=None,
                updated_so_far=updated, failed_so_far=failed,
                inserted_so_far=inserted,
            )


def get_data_status(db_path: Path) -> dict[str, Any]:
    """Summary of data freshness for the UI status bar."""
    with connect(db_path) as conn:
        price_latest = fetch_one(
            conn, "SELECT MAX(time) AS latest FROM stock_price_history", ()
        )
        price_count = fetch_one(
            conn, "SELECT COUNT(DISTINCT symbol) AS n FROM stock_price_history", ()
        )
        ratio_latest = fetch_one(
            conn, "SELECT MAX(year) AS latest_year FROM financial_ratios WHERE quarter IS NULL", ()
        )
        ratio_count = fetch_one(
            conn, "SELECT COUNT(DISTINCT symbol) AS n FROM financial_ratios", ()
        )
    missing_prices = detect_missing_prices(db_path)
    return {
        "price_latest_date": (price_latest or {}).get("latest"),
        "price_symbol_count": (price_count or {}).get("n", 0),
        "ratio_latest_year": (ratio_latest or {}).get("latest_year"),
        "ratio_symbol_count": (ratio_count or {}).get("n", 0),
        "missing_price_count": len(missing_prices),
        "missing_price_symbols": missing_prices[:20],
    }


def get_db_health(db_path: Path) -> dict[str, Any]:
    """Comprehensive DB coverage report for the Data Status UI panel."""
    with connect(db_path) as conn:
        # Total active symbols on exchanges
        total_symbols = fetch_one(
            conn,
            """SELECT COUNT(DISTINCT s.ticker) AS n
               FROM stocks s
               JOIN stock_exchange se ON se.ticker = s.ticker
               WHERE LENGTH(s.ticker) = 3
                 AND s.ticker GLOB '[A-Z][A-Z][A-Z]'
                 AND se.exchange IN ('HSX', 'HNX', 'UPCOM')""",
            (),
        )

        # Price data stats — only count symbols in our exchange universe
        # Filter time to proper YYYY-MM-DD strings (original DB has some integer timestamps)
        price_stats = fetch_one(
            conn,
            """SELECT COUNT(DISTINCT sph.symbol) AS symbols_with_price,
                      COUNT(*) AS total_rows,
                      MIN(sph.time) AS earliest,
                      MAX(sph.time) AS latest
               FROM stock_price_history sph
               WHERE typeof(sph.time) = 'text'
                 AND sph.time GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                 AND sph.symbol IN (
                   SELECT s.ticker FROM stocks s
                   JOIN stock_exchange se ON se.ticker = s.ticker
                   WHERE LENGTH(s.ticker) = 3
                     AND s.ticker GLOB '[A-Z][A-Z][A-Z]'
                     AND se.exchange IN ('HSX', 'HNX', 'UPCOM')
                 )""",
            (),
        )

        # Financial ratio stats — only count symbols in our exchange universe
        ratio_stats = fetch_one(
            conn,
            """SELECT COUNT(DISTINCT fr.symbol) AS symbols_with_ratios,
                      COUNT(*) AS total_rows,
                      MIN(fr.year) AS earliest_year,
                      MAX(fr.year) AS latest_year
               FROM financial_ratios fr
               WHERE fr.quarter IS NULL
                 AND fr.symbol IN (
                   SELECT s.ticker FROM stocks s
                   JOIN stock_exchange se ON se.ticker = s.ticker
                   WHERE LENGTH(s.ticker) = 3
                     AND s.ticker GLOB '[A-Z][A-Z][A-Z]'
                     AND se.exchange IN ('HSX', 'HNX', 'UPCOM')
                 )""",
            (),
        )

        # Per-exchange breakdown
        exchange_breakdown = fetch_all(
            conn,
            """SELECT se.exchange,
                      COUNT(DISTINCT se.ticker) AS total,
                      COUNT(DISTINCT sph.symbol) AS with_price
               FROM stock_exchange se
               JOIN stocks s ON s.ticker = se.ticker
               LEFT JOIN (
                 SELECT DISTINCT symbol FROM stock_price_history
               ) sph ON sph.symbol = se.ticker
               WHERE LENGTH(se.ticker) = 3
                 AND se.ticker GLOB '[A-Z][A-Z][A-Z]'
                 AND se.exchange IN ('HSX', 'HNX', 'UPCOM')
               GROUP BY se.exchange
               ORDER BY se.exchange""",
            (),
        )

    total = (total_symbols or {}).get("n", 0)
    ps = price_stats or {}
    rs = ratio_stats or {}
    symbols_with_price = ps.get("symbols_with_price", 0)
    symbols_with_ratios = rs.get("symbols_with_ratios", 0)

    missing_prices = detect_missing_prices(db_path)
    year = datetime.date.today().year - 1
    missing_financials = detect_missing_financials(db_path, year)

    return {
        "total_symbols": total,
        "price": {
            "symbols_covered": symbols_with_price,
            "total_rows": ps.get("total_rows", 0),
            "earliest_date": ps.get("earliest"),
            "latest_date": ps.get("latest"),
            "missing_count": len(missing_prices),
            "missing_symbols": missing_prices,
            "coverage_pct": round(symbols_with_price / total * 100, 1) if total else 0,
        },
        "financials": {
            "symbols_covered": symbols_with_ratios,
            "total_rows": rs.get("total_rows", 0),
            "earliest_year": rs.get("earliest_year"),
            "latest_year": rs.get("latest_year"),
            "missing_count": len(missing_financials),
            "missing_symbols": [g["ticker"] for g in missing_financials],
            "check_year": year,
            "coverage_pct": round(symbols_with_ratios / total * 100, 1) if total else 0,
        },
        "exchanges": [dict(r) for r in exchange_breakdown],
    }
