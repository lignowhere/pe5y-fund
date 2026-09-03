"""Daily data update orchestrator — fetches missing prices and financials."""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

from ..database.connection import connect, connect_rw, fetch_all, fetch_one
from .vci_client import VCIClient
from .kbs_client import KBSClient

log = logging.getLogger(__name__)

# Re-export financial updater for convenience
from .financial_updater import FinancialProgress, update_financials_stream  # noqa: E402, F401

# Date format expected in stock_price_history.time
_DATE_RE = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}$")

# VCI returns prices in VND; DB convention is thousands of VND
_VCI_PRICE_SCALE = 1000.0
_MARKET_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")
_PRICE_SOURCE_URLS = {
    "VCI": "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart",
}
_SESSION_FINAL_HOUR = 18
_SESSION_FINAL_MINUTE = 30


def _normalize_bars(bars: list[dict]) -> list[dict]:
    """Convert VCI prices from VND to DB scale (thousands of VND)."""
    return [
        {**bar,
         "open": bar["open"] / _VCI_PRICE_SCALE,
         "high": bar["high"] / _VCI_PRICE_SCALE,
         "low": bar["low"] / _VCI_PRICE_SCALE,
         "close": bar["close"] / _VCI_PRICE_SCALE}
        for bar in bars
    ]



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
    max_stale_market_days: int | None = None,
    min_symbols_for_market_day: int = 50,
) -> list[str]:
    """Find actively-traded symbols that are behind the market by trading days.

    Uses actual market trading days (dates with >= *min_symbols_for_market_day*
    symbols) to measure the gap.  A symbol is flagged only if it's >=
    *min_trading_day_gap* market days behind the latest.

    Lower *min_symbols_for_market_day* (e.g. 5) to recognize recent trading
    days that only a handful of symbols have been updated for.  The default 50
    is conservative and avoids anomalous dates.

    Symbols that have been stale for more than *max_stale_market_days* market
    days are excluded — they are likely suspended or delisted.
    """
    with connect(db_path) as conn:
        # Fetch enough market days for both the gap check and staleness limit
        limit = max((max_stale_market_days or 260) + 1, 10)
        has_metadata = _table_exists(conn, "market_price_metadata")
        has_observations = has_metadata and _table_exists(
            conn, "price_source_observations"
        )
        verified_price_clause = (
            """
              AND m.source <> 'LEGACY_UNKNOWN'
              AND m.source_url IS NOT NULL
              AND m.source_url <> ''
              AND m.raw_unit = 'THOUSAND_VND'
              AND m.price_basis IN (
                'current_spot', 'execution_unadjusted'
              )
              AND LENGTH(m.source_payload_sha256) = 64
              AND EXISTS (
                SELECT 1
                FROM price_source_observations observed
                WHERE observed.symbol = h.symbol
                  AND observed.price_date = h.time
                  AND observed.source = m.source
                  AND observed.payload_sha256 =
                      m.source_payload_sha256
                  AND observed.is_session_final = 1
                  AND observed.verification_status = 'verified'
              )
              AND NOT EXISTS (
                SELECT 1
                FROM price_source_observations conflict
                WHERE conflict.symbol = h.symbol
                  AND conflict.price_date = h.time
                  AND conflict.verification_status = 'conflict'
              )
            """
            if has_observations
            else ""
        )
        if has_metadata:
            market_days = fetch_all(
                conn,
                f"""SELECT h.time
                   FROM stock_price_history h
                   LEFT JOIN market_price_metadata m
                      ON m.symbol = h.symbol AND m.price_date = h.time
                   WHERE COALESCE(m.is_provisional, 0) = 0
                     {verified_price_clause}
                   GROUP BY h.time
                   HAVING COUNT(DISTINCT h.symbol) >= ?
                   ORDER BY h.time DESC LIMIT ?""",
                (min_symbols_for_market_day, limit),
            )
        else:
            market_days = fetch_all(
                conn,
                """SELECT time FROM stock_price_history
                   GROUP BY time HAVING COUNT(DISTINCT symbol) >= ?
                   ORDER BY time DESC LIMIT ?""",
                (min_symbols_for_market_day, limit),
            )
        if len(market_days) < min_trading_day_gap:
            return []

        # The Nth most recent market day — symbols behind this are truly stale
        gap_threshold = market_days[min_trading_day_gap - 1]["time"]

        # Staleness cutoff: symbols older than this are likely suspended
        stale_cutoff = None
        if max_stale_market_days and len(market_days) > max_stale_market_days:
            stale_cutoff = market_days[max_stale_market_days]["time"]

        latest_source = f"""
                SELECT h.symbol, MAX(h.time) AS latest
                FROM stock_price_history h
                LEFT JOIN market_price_metadata m
                  ON m.symbol = h.symbol AND m.price_date = h.time
                WHERE COALESCE(m.is_provisional, 0) = 0
                  {verified_price_clause}
                GROUP BY h.symbol
            """ if has_metadata else """
                SELECT symbol, MAX(time) AS latest
                FROM stock_price_history
                WHERE typeof(time) = 'text'
                  AND time GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                GROUP BY symbol
            """
        rows = fetch_all(
            conn,
            f"""
            SELECT s.ticker
            FROM stocks s
            JOIN stock_exchange se ON se.ticker = s.ticker
            LEFT JOIN (
                {latest_source}
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
            bars = _normalize_bars(bars)
            _store_adjusted_bars(db_path, sym, bars)
            sym_inserted = _insert_bars(
                db_path,
                sym,
                _execution_bars_from_vci(bars),
                source="VCI",
                price_basis="current_spot",
            )
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


def _session_is_provisional(price_date: str) -> bool:
    now = datetime.datetime.now(_MARKET_TIMEZONE)
    if price_date != now.date().isoformat():
        return False
    return (now.hour, now.minute) < (
        _SESSION_FINAL_HOUR,
        _SESSION_FINAL_MINUTE,
    )


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = ?""",
            (table,),
        ).fetchone()
    )


def _record_source_health(
    db_path: Path,
    source: str,
    capability: str,
    available: bool,
    error: Exception | None = None,
) -> None:
    try:
        response = getattr(error, "response", None)
        status_code = (
            getattr(error, "status_code", None)
            or getattr(response, "status_code", None)
        )
        with connect_rw(db_path) as conn:
            if not _table_exists(conn, "data_source_health"):
                return
            conn.execute(
                """INSERT INTO data_source_health
                   (source, capability, available, last_status_code,
                    last_error, checked_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source, capability) DO UPDATE SET
                     available = excluded.available,
                     last_status_code = excluded.last_status_code,
                     last_error = excluded.last_error,
                     checked_at = excluded.checked_at""",
                (
                    source,
                    capability,
                    int(available),
                    status_code,
                    str(error)[:500] if error else None,
                    datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                ),
            )
    except Exception:
        log.debug("Could not persist source health", exc_info=True)


def _do_insert_bars(
    conn,
    sym: str,
    bars: list[dict],
    *,
    source: str,
    price_basis: str,
) -> int:
    """Upsert bars and retain source/completion metadata."""
    count = 0
    has_metadata = _table_exists(conn, "market_price_metadata")
    has_observations = _table_exists(
        conn, "price_source_observations"
    )
    for bar in bars:
        date_str = _ts_to_date(bar.get("time"))
        if date_str is None:
            continue
        values = (
            bar["open"],
            bar["high"],
            bar["low"],
            bar["close"],
            bar["volume"],
            sym,
            date_str,
        )
        cur = conn.execute(
            """UPDATE stock_price_history
               SET open = ?, high = ?, low = ?, close = ?, volume = ?
               WHERE symbol = ? AND time = ?""",
            values,
        )
        if cur.rowcount == 0:
            conn.execute(
                """INSERT INTO stock_price_history
                   (symbol, time, open, high, low, close, volume)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    sym,
                    date_str,
                    bar["open"],
                    bar["high"],
                    bar["low"],
                    bar["close"],
                    bar["volume"],
                ),
            )
        count += 1
        if has_metadata:
            payload_sha256 = hashlib.sha256(
                json.dumps(
                    {
                        "symbol": sym,
                        "date": date_str,
                        "open": bar["open"],
                        "high": bar["high"],
                        "low": bar["low"],
                        "close": bar["close"],
                        "volume": bar["volume"],
                        "source": source,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            provisional = int(_session_is_provisional(date_str))
            observed_at = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
            source_url = _PRICE_SOURCE_URLS.get(source)
            if has_observations:
                conn.execute(
                    """INSERT OR IGNORE INTO price_source_observations
                       (symbol, price_date, open_vnd, high_vnd, low_vnd,
                        close_vnd, volume, source, source_url,
                        payload_sha256, observed_at, is_session_final,
                        verification_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               'verified')""",
                    (
                        sym,
                        date_str,
                        float(bar["open"]) * 1000.0,
                        float(bar["high"]) * 1000.0,
                        float(bar["low"]) * 1000.0,
                        float(bar["close"]) * 1000.0,
                        float(bar["volume"]),
                        source,
                        source_url,
                        payload_sha256,
                        observed_at,
                        int(not provisional),
                    ),
                )
            conn.execute(
                """INSERT INTO market_price_metadata
                   (symbol, price_date, source, price_basis, raw_unit,
                    is_provisional, observed_at, source_url,
                    source_payload_sha256)
                   VALUES (?, ?, ?, ?, 'THOUSAND_VND', ?, ?, ?, ?)
                   ON CONFLICT(symbol, price_date) DO UPDATE SET
                     source = excluded.source,
                     price_basis = excluded.price_basis,
                     raw_unit = excluded.raw_unit,
                     is_provisional = excluded.is_provisional,
                     observed_at = excluded.observed_at,
                     source_url = excluded.source_url,
                     source_payload_sha256 =
                       excluded.source_payload_sha256""",
                (
                    sym,
                    date_str,
                    source,
                    price_basis,
                    provisional,
                    observed_at,
                    source_url,
                    payload_sha256,
                ),
            )
    return count


def _insert_bars(
    db_path: Path,
    sym: str,
    bars: list[dict],
    conn=None,
    *,
    source: str = "UNKNOWN",
    price_basis: str = "execution_unadjusted",
) -> int:
    """Upsert OHLCV bars using an optional existing connection."""
    if conn is not None:
        return _do_insert_bars(
            conn,
            sym,
            bars,
            source=source,
            price_basis=price_basis,
        )
    with connect_rw(db_path) as active:
        return _do_insert_bars(
            active,
            sym,
            bars,
            source=source,
            price_basis=price_basis,
        )


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


def _store_adjusted_bars(
    db_path: Path,
    sym: str,
    normalized_bars: list[dict],
) -> None:
    """Persist VCI gap-chart history outside the execution-price table."""
    source_as_of = _latest_bar_date(normalized_bars)
    if not source_as_of:
        return
    rows = []
    for bar in normalized_bars:
        price_date = _ts_to_date(bar.get("time"))
        close = bar.get("close")
        if not price_date or close is None or float(close) <= 0:
            continue
        rows.append(
            (
                sym,
                price_date,
                float(close) * _VCI_PRICE_SCALE,
                "VCI_GAP_CHART",
                "adjusted_total_return",
                source_as_of,
            )
        )
    if not rows:
        return
    with connect_rw(db_path) as conn:
        if not _table_exists(conn, "adjusted_price_history"):
            return
        conn.executemany(
            """INSERT INTO adjusted_price_history
               (symbol, price_date, close_vnd, source, price_basis,
                source_as_of)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, price_date, source_as_of) DO UPDATE SET
                 close_vnd = excluded.close_vnd,
                 fetched_at = CURRENT_TIMESTAMP""",
            rows,
        )


def _execution_bars_from_vci(bars: list[dict]) -> list[dict]:
    """Use only VCI's newest point as an execution/spot price."""
    latest = _latest_bar_date(bars)
    if not latest:
        return []
    return [
        bar for bar in bars if _ts_to_date(bar.get("time")) == latest
    ]


def probe_price_fallback(db_path: Path, kbs: KBSClient) -> bool:
    """Run one bounded KBS contract smoke test and persist its result."""
    try:
        bars = kbs.get_ohlcv("VNINDEX", count_back=2)
        available = bool(bars)
        _record_source_health(
            db_path,
            "KBS",
            "prices",
            available,
            None if available else RuntimeError("KBS returned no OHLC bars"),
        )
        return available
    except Exception as exc:
        _record_source_health(db_path, "KBS", "prices", False, exc)
        return False


def update_prices_stream(
    db_path: Path,
    symbols: list[str],
    vci: VCIClient,
    kbs: Optional[KBSClient] = None,
    count_back: int = 30,
    max_workers: int = 2,
) -> Iterator[SymbolProgress]:
    """Like update_prices but yields progress per symbol for SSE streaming.

    Uses ThreadPoolExecutor for parallel fetching — overlaps HTTP I/O across
    workers while respecting API rate limits via thread-safe throttling.

    KBS is probed only as an independent comparison source. Its rows are
    never mixed automatically into the canonical VCI series; a discrepancy
    is surfaced as an error so snapshot promotion can fail closed.
    """
    total = len(symbols)
    if total == 0:
        return

    # Shared mutable counters protected by lock
    state = {"updated": 0, "failed": 0, "inserted": 0}
    counter_lock = threading.Lock()

    def _process_one(idx: int, sym: str) -> SymbolProgress:
        """Fetch one VCI symbol without mixing fallback-source rows."""
        sym_bars = 0
        skip_reason = None
        source = "VCI"
        bars = None

        try:
            bars = vci.get_ohlcv(sym, count_back=count_back)
            if bars:
                bars = _normalize_bars(bars)
                _store_adjusted_bars(db_path, sym, bars)

            if bars:
                sym_bars = _insert_bars(
                    db_path,
                    sym,
                    _execution_bars_from_vci(bars),
                    source="VCI",
                    price_basis="current_spot",
                )
            elif kbs is not None:
                try:
                    comparison = kbs.get_ohlcv(sym, count_back=count_back)
                    _record_source_health(
                        db_path, "KBS", "prices", bool(comparison),
                        None if comparison else RuntimeError(
                            "KBS returned no OHLC bars"
                        )
                    )
                    if comparison:
                        raise RuntimeError(
                            "VCI returned no bars while KBS had data; "
                            "automatic source mixing is blocked"
                        )
                except Exception as exc:
                    if "automatic source mixing is blocked" in str(exc):
                        raise
                    _record_source_health(db_path, "KBS", "prices", False, exc)

            with counter_lock:
                if not bars and sym_bars == 0:
                    skip_reason = "VCI returned 0 bars"
                    return SymbolProgress(
                        symbol=sym, index=idx, total=total, status="skip",
                        bars_inserted=0, error=None, skip_reason=skip_reason,
                        updated_so_far=state["updated"],
                        failed_so_far=state["failed"],
                        inserted_so_far=state["inserted"],
                    )

                state["inserted"] += sym_bars
                if sym_bars > 0:
                    state["updated"] += 1
                    status = "ok"
                else:
                    status = "skip"
                    latest = _latest_bar_date(bars) if bars else "?"
                    skip_reason = (
                        f"no new data from {source} (latest: {latest})"
                    )

                return SymbolProgress(
                    symbol=sym, index=idx, total=total, status=status,
                    bars_inserted=sym_bars, error=None, skip_reason=skip_reason,
                    updated_so_far=state["updated"],
                    failed_so_far=state["failed"],
                    inserted_so_far=state["inserted"],
                )
        except Exception as e:
            _record_source_health(db_path, "VCI", "prices", False, e)
            with counter_lock:
                state["failed"] += 1
                err_msg = f"{sym}: {e}"
                log.warning("Failed to update prices for %s: %s", sym, e)
                return SymbolProgress(
                    symbol=sym, index=idx, total=total, status="error",
                    bars_inserted=0, error=err_msg, skip_reason=None,
                    updated_so_far=state["updated"],
                    failed_so_far=state["failed"],
                    inserted_so_far=state["inserted"],
                )

    # Keep only a bounded number of submitted jobs.  Besides reducing memory,
    # this lets a cancelled consumer close the generator after waiting for at
    # most ``max_workers`` active HTTP requests instead of the whole universe.
    work = iter(enumerate(symbols))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for _ in range(max_workers):
            try:
                idx, sym = next(work)
            except StopIteration:
                break
            futures[executor.submit(_process_one, idx, sym)] = idx
        while futures:
            future = next(as_completed(tuple(futures)))
            futures.pop(future, None)
            yield future.result()
            try:
                idx, sym = next(work)
            except StopIteration:
                continue
            futures[executor.submit(_process_one, idx, sym)] = idx


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


def _compute_db_health(db_path: Path) -> dict[str, Any]:
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

    # Aggressive detection: all symbols behind the latest real trading day
    all_behind = detect_missing_prices(
        db_path, min_trading_day_gap=1, min_symbols_for_market_day=5,
    )
    # Conservative: only symbols behind by 3+ days with broad coverage
    stale_prices = detect_missing_prices(db_path)  # defaults: gap=3, threshold=50
    stale_set = set(stale_prices)
    # Split: "behind market" (updatable, just 1-2 days behind) vs "stale" (illiquid)
    behind_market = [s for s in all_behind if s not in stale_set]
    year = datetime.date.today().year - 1
    missing_financials = detect_missing_financials(db_path, year)

    return {
        "total_symbols": total,
        "price": {
            "symbols_covered": symbols_with_price,
            "total_rows": ps.get("total_rows", 0),
            "earliest_date": ps.get("earliest"),
            "latest_date": ps.get("latest"),
            "missing_count": len(all_behind),
            "missing_symbols": all_behind,
            "behind_count": len(behind_market),
            "behind_symbols": behind_market,
            "stale_count": len(stale_prices),
            "stale_symbols": stale_prices,
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


def refresh_data_health_summary(db_path: Path) -> dict[str, Any]:
    """Recompute and cache the expensive coverage report."""
    summary = _compute_db_health(db_path)
    with connect_rw(db_path) as conn:
        if _table_exists(conn, "market_price_metadata"):
            completed = fetch_one(
                conn,
                """SELECT h.time, COUNT(DISTINCT h.symbol) AS symbols
                   FROM stock_price_history h
                   LEFT JOIN market_price_metadata m
                     ON m.symbol = h.symbol AND m.price_date = h.time
                   WHERE COALESCE(m.is_provisional, 0) = 0
                   GROUP BY h.time
                   HAVING COUNT(DISTINCT h.symbol) >= 100
                   ORDER BY h.time DESC LIMIT 1""",
            )
            provisional = fetch_one(
                conn,
                """SELECT COUNT(*) AS rows,
                          COUNT(DISTINCT symbol) AS symbols,
                          MAX(price_date) AS latest
                   FROM market_price_metadata
                   WHERE is_provisional = 1""",
            )
            source_health = fetch_all(
                conn,
                """SELECT source, capability, available, last_status_code,
                          last_error, checked_at
                   FROM data_source_health
                   ORDER BY source, capability""",
            )
        else:
            completed = None
            provisional = None
            source_health = []
        total = int(summary.get("total_symbols") or 0)
        current_symbols = int((completed or {}).get("symbols") or 0)
        summary["price"]["historical_coverage_pct"] = summary["price"][
            "coverage_pct"
        ]
        summary["price"]["completed_session"] = (
            completed or {}
        ).get("time")
        summary["price"]["completed_session_symbols"] = current_symbols
        summary["price"]["coverage_pct"] = (
            round(current_symbols / total * 100, 1) if total else 0
        )
        summary["price"]["provisional"] = provisional or {
            "rows": 0,
            "symbols": 0,
            "latest": None,
        }
        summary["source_health"] = source_health
        summary["fallback_available"] = any(
            row["source"] == "KBS"
            and row["capability"] == "prices"
            and bool(row["available"])
            for row in source_health
        )
        payload = __import__("json").dumps(
            summary, ensure_ascii=False, separators=(",", ":")
        )
        conn.execute(
            """INSERT INTO data_health_summary
               (id, summary_json, refreshed_at)
               VALUES (1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 summary_json = excluded.summary_json,
                 refreshed_at = excluded.refreshed_at""",
            (
                payload,
                datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat(timespec="seconds"),
            ),
        )
    return summary


def get_db_health(db_path: Path) -> dict[str, Any]:
    """Return the cached coverage report, computing it only when absent."""
    with connect(db_path) as conn:
        if _table_exists(conn, "data_health_summary"):
            row = fetch_one(
                conn,
                """SELECT summary_json, refreshed_at
                   FROM data_health_summary WHERE id = 1""",
            )
            if row:
                try:
                    summary = __import__("json").loads(row["summary_json"])
                    summary["refreshed_at"] = row["refreshed_at"]
                    summary["cached"] = True
                    return summary
                except (TypeError, ValueError):
                    pass
    summary = refresh_data_health_summary(db_path)
    summary["cached"] = False
    return summary
