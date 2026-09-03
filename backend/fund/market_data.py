"""Price queries used by the current-NAV fund planner."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from ..database.connection import connect, fetch_all, fetch_one


def strategy_timing(
    db_path: Path,
    rebalance_date: str,
    benchmark_symbol: str = "VNINDEX",
) -> dict[str, str]:
    """Return the last knowable close and the next executable session."""
    with connect(db_path) as conn:
        signal = fetch_one(
            conn,
            """SELECT MAX(time) AS price_date
               FROM stock_price_history
               WHERE symbol = ? AND time < ?
                 AND close IS NOT NULL""",
            (benchmark_symbol, rebalance_date),
        )
        execution = fetch_one(
            conn,
            """SELECT MIN(time) AS price_date
               FROM stock_price_history
               WHERE symbol = ? AND time >= ?
                 AND open IS NOT NULL""",
            (benchmark_symbol, rebalance_date),
        )
    signal_date = (signal or {}).get("price_date")
    execution_date = (execution or {}).get("price_date")
    if not signal_date or not execution_date:
        raise ValueError(
            f"Không xác định được lịch tín hiệu/thực thi quanh {rebalance_date}"
        )
    return {
        "signal_price_date": str(signal_date),
        # 15:00 Asia/Ho_Chi_Minh is 08:00 UTC. Provenance timestamps are
        # normalized to UTC before PIT comparisons.
        "signal_cutoff": f"{signal_date}T08:00:00Z",
        "execution_date": str(execution_date),
    }


def last_closes_before(
    db_path: Path,
    symbols: list[str],
    target_date: str,
    scale_vnd: float,
) -> dict[str, dict[str, Any]]:
    """Return each symbol's latest completed unadjusted close before a date."""
    if not symbols:
        return {}
    placeholders = ",".join("?" for _ in symbols)
    with connect(db_path) as conn:
        rows = fetch_all(
            conn,
            f"""SELECT h.symbol, h.time, h.close
                FROM stock_price_history h
                JOIN (
                    SELECT p.symbol, MAX(p.time) AS price_date
                    FROM stock_price_history p
                    LEFT JOIN market_price_metadata m
                      ON m.symbol = p.symbol AND m.price_date = p.time
                    WHERE p.symbol IN ({placeholders})
                      AND p.time < ? AND p.close IS NOT NULL
                      AND COALESCE(m.is_provisional, 0) = 0
                      AND COALESCE(
                            m.price_basis, 'legacy_unknown'
                          ) <> 'adjusted_total_return'
                    GROUP BY p.symbol
                ) x ON x.symbol = h.symbol AND x.price_date = h.time""",
            (*symbols, target_date),
        )
    return {
        row["symbol"]: {
            "price_vnd": float(row["close"]) * scale_vnd,
            "price_date": row["time"],
        }
        for row in rows
    }


def opens_on_date(
    db_path: Path,
    symbols: list[str],
    price_date: str,
    scale_vnd: float,
) -> dict[str, dict[str, Any]]:
    """Return verified, unadjusted opening prices for one session."""
    if not symbols:
        return {}
    placeholders = ",".join("?" for _ in symbols)
    with connect(db_path) as conn:
        rows = fetch_all(
            conn,
            f"""SELECT h.symbol, h.time, h.open, m.source,
                       m.price_basis, m.raw_unit, m.is_provisional,
                       m.observed_at
                FROM stock_price_history h
                LEFT JOIN market_price_metadata m
                  ON m.symbol = h.symbol AND m.price_date = h.time
                WHERE h.symbol IN ({placeholders})
                  AND h.time = ? AND h.open IS NOT NULL
                  AND COALESCE(m.is_provisional, 0) = 0
                  AND COALESCE(
                        m.price_basis, 'legacy_unknown'
                      ) <> 'adjusted_total_return'""",
            (*symbols, price_date),
        )
    return {
        row["symbol"]: {
            "price_vnd": float(row["open"]) * scale_vnd,
            "price_date": row["time"],
            "source": row.get("source"),
            "price_basis": row.get("price_basis"),
            "raw_unit": row.get("raw_unit"),
            "is_provisional": bool(row.get("is_provisional") or 0),
            "observed_at": row.get("observed_at"),
        }
        for row in rows
    }


def first_prices_on_or_after(
    db_path: Path,
    symbols: list[str],
    target_date: str,
    max_gap_days: int,
    scale_vnd: float,
) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}
    end_date = (
        dt.date.fromisoformat(target_date) + dt.timedelta(days=max_gap_days)
    ).isoformat()
    placeholders = ",".join("?" for _ in symbols)
    with connect(db_path) as conn:
        has_metadata = conn.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = 'market_price_metadata'"""
        ).fetchone()
        if has_metadata:
            rows = fetch_all(
                conn,
                f"""SELECT h.symbol, h.time, h.close
                    FROM stock_price_history h
                    JOIN (
                        SELECT p.symbol, MIN(p.time) AS first_time
                        FROM stock_price_history p
                        LEFT JOIN market_price_metadata m
                          ON m.symbol = p.symbol
                         AND m.price_date = p.time
                        WHERE p.symbol IN ({placeholders})
                          AND p.time >= ? AND p.time <= ?
                          AND p.close IS NOT NULL
                          AND COALESCE(m.is_provisional, 0) = 0
                          AND COALESCE(
                                m.price_basis, 'legacy_unknown'
                              ) <> 'adjusted_total_return'
                        GROUP BY p.symbol
                    ) x ON x.symbol = h.symbol AND x.first_time = h.time""",
                (*symbols, target_date, end_date),
            )
        else:
            rows = fetch_all(
                conn,
                f"""SELECT h.symbol, h.time, h.close
                    FROM stock_price_history h
                    JOIN (
                        SELECT symbol, MIN(time) AS first_time
                        FROM stock_price_history
                        WHERE symbol IN ({placeholders})
                          AND time >= ? AND time <= ?
                          AND close IS NOT NULL
                        GROUP BY symbol
                    ) x ON x.symbol = h.symbol AND x.first_time = h.time""",
                (*symbols, target_date, end_date),
            )
    return {
        row["symbol"]: {
            "price_vnd": float(row["close"]) * scale_vnd,
            "price_date": row["time"],
        }
        for row in rows
    }


def prices_on_date(
    db_path: Path,
    symbols: list[str],
    price_date: str,
    scale_vnd: float,
    *,
    require_verified: bool = False,
    expected_source: str | None = None,
) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}
    placeholders = ",".join("?" for _ in symbols)
    with connect(db_path) as conn:
        has_metadata = conn.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = 'market_price_metadata'"""
        ).fetchone()
        if has_metadata:
            verified_clause = ""
            verified_params: tuple[Any, ...] = ()
            if require_verified:
                verified_clause = """
                      AND m.source IS NOT NULL
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
                if expected_source:
                    verified_clause += " AND m.source = ?"
                    verified_params = (expected_source,)
            rows = fetch_all(
                conn,
                f"""SELECT h.symbol, h.time, h.close
                    FROM stock_price_history h
                    LEFT JOIN market_price_metadata m
                      ON m.symbol = h.symbol AND m.price_date = h.time
                    WHERE h.symbol IN ({placeholders})
                      AND h.time = ? AND h.close IS NOT NULL
                      AND COALESCE(m.is_provisional, 0) = 0
                      AND COALESCE(
                            m.price_basis, 'legacy_unknown'
                          ) <> 'adjusted_total_return'
                      {verified_clause}""",
                (*symbols, price_date, *verified_params),
            )
        else:
            rows = fetch_all(
                conn,
                f"""SELECT symbol, time, close
                    FROM stock_price_history
                    WHERE symbol IN ({placeholders})
                      AND time = ? AND close IS NOT NULL""",
                (*symbols, price_date),
            )
    return {
        row["symbol"]: {
            "price_vnd": float(row["close"]) * scale_vnd,
            "price_date": row["time"],
        }
        for row in rows
    }


def vendor_adjusted_price_pairs(
    db_path: Path,
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> dict[str, dict[str, Any]]:
    """Load same-vintage adjusted closes for a research-only return series.

    The latest vintage that contains both requested dates for *every* symbol
    is selected.  This prevents a partial daily refresh from mixing different
    corporate-action vintages inside one portfolio calculation.
    """
    normalized = sorted({symbol.strip().upper() for symbol in symbols if symbol})
    if not normalized:
        return {}
    placeholders = ",".join("?" for _ in normalized)
    with connect(db_path) as conn:
        rows = fetch_all(
            conn,
            f"""WITH complete_vintages AS (
                    SELECT symbol, source_as_of
                    FROM adjusted_price_history
                    WHERE symbol IN ({placeholders})
                      AND price_date IN (?, ?)
                      AND price_basis = 'adjusted_total_return'
                    GROUP BY symbol, source_as_of
                    HAVING COUNT(DISTINCT price_date) = 2
                ),
                common_vintage AS (
                    SELECT source_as_of
                    FROM complete_vintages
                    GROUP BY source_as_of
                    HAVING COUNT(DISTINCT symbol) = ?
                    ORDER BY source_as_of DESC
                    LIMIT 1
                )
                SELECT p.symbol, p.price_date, p.close_vnd, p.source,
                       p.price_basis, p.source_as_of
                FROM adjusted_price_history p
                JOIN common_vintage v
                  ON v.source_as_of = p.source_as_of
                WHERE p.symbol IN ({placeholders})
                  AND p.price_date IN (?, ?)
                ORDER BY p.symbol, p.price_date""",
            (
                *normalized,
                start_date,
                end_date,
                len(normalized),
                *normalized,
                start_date,
                end_date,
            ),
        )
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = grouped.setdefault(
            str(row["symbol"]),
            {
                "source": row["source"],
                "source_as_of": row["source_as_of"],
                "price_basis": row["price_basis"],
            },
        )
        if row["source"] != item["source"]:
            grouped.pop(str(row["symbol"]), None)
            continue
        label = "start_value" if row["price_date"] == start_date else "end_value"
        item[label] = float(row["close_vnd"])
    return {
        symbol: item
        for symbol, item in grouped.items()
        if item.get("start_value", 0) > 0 and item.get("end_value", 0) > 0
    }


def verified_benchmark_total_return_pair(
    db_path: Path,
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    max_end_gap_days: int = 0,
) -> dict[str, Any] | None:
    """Return one conflict-free official total-return benchmark pair."""
    end_limit = (
        dt.date.fromisoformat(end_date)
        + dt.timedelta(days=max_end_gap_days)
    ).isoformat()
    with connect(db_path) as conn:
        table = fetch_one(
            conn,
            """SELECT 1 AS present FROM sqlite_master
               WHERE type = 'table'
                 AND name = 'benchmark_total_return_history'""",
        )
        if not table:
            return None
        start = fetch_one(
            conn,
            """SELECT price_date, index_value, source_authority,
                      document_sha256
               FROM benchmark_total_return_history b
               WHERE symbol = ? AND price_date = ?
                 AND verification_status = 'verified'
                 AND NOT EXISTS (
                   SELECT 1
                   FROM benchmark_total_return_history conflict
                   WHERE conflict.symbol = b.symbol
                     AND conflict.price_date = b.price_date
                     AND conflict.verification_status = 'conflict'
                 )
               ORDER BY id DESC LIMIT 1""",
            (symbol, start_date),
        )
        end = fetch_one(
            conn,
            """SELECT price_date, index_value, source_authority,
                      document_sha256
               FROM benchmark_total_return_history b
               WHERE symbol = ?
                 AND price_date >= ? AND price_date <= ?
                 AND verification_status = 'verified'
                 AND NOT EXISTS (
                   SELECT 1
                   FROM benchmark_total_return_history conflict
                   WHERE conflict.symbol = b.symbol
                     AND conflict.price_date = b.price_date
                     AND conflict.verification_status = 'conflict'
                 )
               ORDER BY price_date, id DESC LIMIT 1""",
            (symbol, end_date, end_limit),
        )
    if not start or not end:
        return None
    if start["source_authority"] != end["source_authority"]:
        return None
    return {
        "symbol": symbol,
        "start_date": start["price_date"],
        "start_value": float(start["index_value"]),
        "end_date": end["price_date"],
        "end_value": float(end["index_value"]),
        "source_authority": start["source_authority"],
        "start_document_sha256": start["document_sha256"],
        "end_document_sha256": end["document_sha256"],
    }
