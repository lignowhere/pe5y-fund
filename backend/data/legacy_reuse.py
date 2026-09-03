"""Reuse and classify the existing database without weakening safety gates.

The legacy database is valuable as a comparison baseline and as a source of
already-downloaded vendor rows.  It is not documentary provenance.  This
module therefore has two deliberately separate responsibilities:

* reconcile vendor research versions by filling missing keys only; and
* create an inspectable queue describing which official evidence is still
  required before strict PIT snapshots can be activated.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..database.connection import connect, connect_rw, fetch_all, fetch_one
from ..fund.market_data import strategy_timing
from .db_migration import _create_legacy_reuse_tables
from .financial_snapshot import (
    VENDOR_RESEARCH_METHODOLOGY,
    _required_investment_symbols,
)


@dataclass(frozen=True)
class LegacyReuseResult:
    run_id: int
    reused_existing_run: bool
    universe_count: int
    financial_baseline_symbols: int
    financial_missing_symbols: int
    cycle_price_complete: int
    cycle_price_incomplete: int
    queue_items: int
    source_fingerprint: str


def get_legacy_reuse_status(db_path: Path) -> dict[str, Any]:
    """Return the latest inventory summary and outstanding work by type."""
    with connect(db_path) as conn:
        exists = fetch_one(
            conn,
            """SELECT 1 AS present FROM sqlite_master
               WHERE type = 'table' AND name = 'legacy_reuse_runs'""",
        )
        if not exists:
            return {
                "available": False,
                "strict_pit_eligible": False,
                "message": "Legacy reuse inventory has not been built",
            }
        run = fetch_one(
            conn,
            """SELECT * FROM legacy_reuse_runs
               ORDER BY id DESC LIMIT 1""",
        )
        if not run:
            return {
                "available": False,
                "strict_pit_eligible": False,
                "message": "Legacy reuse inventory has not been built",
            }
        queue = fetch_all(
            conn,
            """SELECT evidence_type, verification_status,
                      baseline_status, COUNT(*) AS items
               FROM legacy_verification_queue
               WHERE run_id = ?
               GROUP BY evidence_type, verification_status, baseline_status
               ORDER BY evidence_type, verification_status, baseline_status""",
            (int(run["id"]),),
        )
    return {
        "available": run["status"] == "completed",
        "strict_pit_eligible": False,
        "run": run,
        "queue_summary": queue,
    }


def reconcile_vendor_research_versions(db_path: Path) -> dict[str, Any]:
    """Fill missing vendor keys from stored versions without overwriting.

    A new immutable vendor-research version is created from the union.  Rows
    in the current active research version always win; another stored version
    may only fill a missing ``symbol/year/quarter`` key.  The result remains
    explicitly non-PIT and non-official, so it cannot unlock the planner.
    """
    with connect(db_path) as conn:
        active = fetch_one(
            conn,
            """SELECT id FROM financial_data_versions
               WHERE is_active = 1 ORDER BY id DESC LIMIT 1""",
        )
        if not active:
            raise ValueError("No active vendor research version")
        active_id = int(active["id"])
        rows = fetch_all(
            conn,
            """WITH candidates AS (
                   SELECT r.*,
                          ROW_NUMBER() OVER (
                              PARTITION BY r.symbol, r.year,
                                           COALESCE(r.quarter, 0)
                              ORDER BY
                                  CASE WHEN r.financial_data_version_id = ?
                                       THEN 0 ELSE 1 END,
                                  r.financial_data_version_id DESC,
                                  r.id DESC
                          ) AS choice
                   FROM financial_ratio_versions r
                   JOIN financial_data_versions v
                     ON v.id = r.financial_data_version_id
                   WHERE UPPER(COALESCE(r.source, 'VCI')) = 'VCI'
                     AND COALESCE(v.official_provenance_ready, 0) = 0
               )
               SELECT * FROM candidates
               WHERE choice = 1
               ORDER BY symbol, year, COALESCE(quarter, 0)""",
            (active_id,),
        )
        active_count = int(
            (
                fetch_one(
                    conn,
                    """SELECT COUNT(*) AS n
                       FROM financial_ratio_versions
                       WHERE financial_data_version_id = ?""",
                    (active_id,),
                )
                or {}
            ).get("n")
            or 0
        )

    if not rows:
        raise ValueError("No stored vendor financial rows")

    content_hash = _financial_rows_hash(rows)
    recovered = max(0, len(rows) - active_count)
    symbols = len({str(row["symbol"]) for row in rows})
    as_of_year = max(int(row["year"]) for row in rows)
    as_of_quarter = max(
        (
            int(row["quarter"])
            for row in rows
            if int(row["year"]) == as_of_year
            and row.get("quarter") is not None
        ),
        default=4,
    )
    with connect_rw(db_path) as conn:
        existing = fetch_one(
            conn,
            """SELECT id FROM financial_data_versions
               WHERE content_hash = ?""",
            (content_hash,),
        )
        if existing:
            version_id = int(existing["id"])
        else:
            cursor = conn.execute(
                """INSERT INTO financial_data_versions
                   (source, source_api, as_of_year, as_of_quarter,
                    content_hash, row_count, symbol_count, is_active,
                    point_in_time_ready, publication_coverage_pct,
                    verified_row_count, methodology_version,
                    official_provenance_ready, quality_status,
                    quality_issues_json)
                   VALUES (
                       'VCI', 'stored_vendor_union_v1', ?, ?, ?, ?, ?, 0,
                       0, 0, 0, ?, 0, 'vendor_research_reconciled',
                       ?)""",
                (
                    as_of_year,
                    as_of_quarter,
                    content_hash,
                    len(rows),
                    symbols,
                    VENDOR_RESEARCH_METHODOLOGY,
                    json.dumps(
                        [
                            "OFFICIAL_DOCUMENT_REQUIRED",
                            "MISSING_KEYS_FILLED_FROM_STORED_VERSIONS",
                        ],
                        separators=(",", ":"),
                    ),
                ),
            )
            version_id = int(cursor.lastrowid)
            conn.executemany(
                """INSERT INTO financial_ratio_versions
                   (financial_data_version_id, symbol, period, year,
                    quarter, price_to_book, price_to_earnings, eps_vnd,
                    bvps_vnd, roe, market_cap_billions,
                    shares_outstanding_millions, data_json, source,
                    public_date, source_created_at, source_updated_at,
                    available_at, publication_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?)""",
                [
                    (
                        version_id,
                        row["symbol"],
                        row["period"],
                        row["year"],
                        row["quarter"],
                        row["price_to_book"],
                        row["price_to_earnings"],
                        row["eps_vnd"],
                        row["bvps_vnd"],
                        row["roe"],
                        row["market_cap_billions"],
                        row["shares_outstanding_millions"],
                        row["data_json"],
                        row["source"],
                        row["public_date"],
                        row["source_created_at"],
                        row["source_updated_at"],
                        row["available_at"],
                        row["publication_status"],
                    )
                    for row in rows
                ],
            )

        current = fetch_one(
            conn,
            """SELECT id FROM financial_data_versions
               WHERE is_active = 1 ORDER BY id DESC LIMIT 1""",
        )
        changed = int((current or {}).get("id") or 0) != version_id
        if changed:
            conn.execute(
                """DELETE FROM financial_ratios"""
            )
            conn.execute(
                """INSERT INTO financial_ratios
                   (symbol, period, year, quarter,
                    price_to_book, price_to_earnings, eps_vnd,
                    bvps_vnd, roe, market_cap_billions,
                    shares_outstanding_millions, data_json, source,
                    public_date, source_created_at, source_updated_at,
                    available_at, publication_status,
                    financial_data_version_id)
                   SELECT symbol, period, year, quarter,
                          price_to_book, price_to_earnings, eps_vnd,
                          bvps_vnd, roe, market_cap_billions,
                          shares_outstanding_millions, data_json, source,
                          public_date, source_created_at, source_updated_at,
                          available_at, publication_status, ?
                   FROM financial_ratio_versions
                   WHERE financial_data_version_id = ?
                   ORDER BY symbol, year, COALESCE(quarter, 0)""",
                (version_id, version_id),
            )
            conn.execute(
                """UPDATE financial_data_versions
                   SET is_active = CASE WHEN id = ? THEN 1 ELSE 0 END
                   WHERE is_active = 1 OR id = ?""",
                (version_id, version_id),
            )
    return {
        "previous_version_id": active_id,
        "version_id": version_id,
        "row_count": len(rows),
        "symbol_count": symbols,
        "missing_keys_recovered": recovered,
        "changed": bool(changed),
        "point_in_time_ready": False,
        "official_provenance_ready": False,
        "content_hash": content_hash,
    }


def build_legacy_reuse_inventory(
    db_path: Path,
    *,
    start_year: int,
    end_year: int,
    focus_symbols: Iterable[str] = (),
) -> LegacyReuseResult:
    """Inventory reusable rows and create a minimal official-evidence queue."""
    if start_year > end_year:
        raise ValueError("start_year must be <= end_year")
    _create_legacy_reuse_tables(db_path)
    universe = sorted(_required_investment_symbols(db_path))
    focus = {value.strip().upper() for value in focus_symbols if value.strip()}
    focus.update(_stored_focus_symbols(db_path, start_year, end_year))
    timings = {
        year: strategy_timing(db_path, f"{year}-09-01")
        for year in range(start_year, end_year + 1)
    }
    fingerprint = _source_fingerprint(
        db_path, start_year, end_year, universe, timings, focus
    )
    with connect(db_path) as conn:
        existing = fetch_one(
            conn,
            """SELECT id, stats_json FROM legacy_reuse_runs
               WHERE source_fingerprint = ?
                 AND start_year = ? AND end_year = ?
                 AND status = 'completed'""",
            (fingerprint, start_year, end_year),
        )
    if existing:
        return _result_from_stats(
            int(existing["id"]),
            True,
            fingerprint,
            json.loads(str(existing["stats_json"])),
        )

    with connect_rw(db_path) as conn:
        previous = fetch_one(
            conn,
            """SELECT id, status FROM legacy_reuse_runs
               WHERE source_fingerprint = ?
                 AND start_year = ? AND end_year = ?""",
            (fingerprint, start_year, end_year),
        )
        if previous and previous["status"] == "running":
            raise RuntimeError(
                "An identical legacy reuse inventory is already running"
            )
        if previous:
            run_id = int(previous["id"])
            conn.execute(
                """DELETE FROM legacy_verification_queue WHERE run_id = ?""",
                (run_id,),
            )
            conn.execute(
                """DELETE FROM legacy_cycle_inventory WHERE run_id = ?""",
                (run_id,),
            )
            conn.execute(
                """DELETE FROM legacy_symbol_inventory WHERE run_id = ?""",
                (run_id,),
            )
            conn.execute(
                """UPDATE legacy_reuse_runs
                   SET status = 'running', universe_count = 0,
                       stats_json = '{}', error = NULL, completed_at = NULL
                   WHERE id = ?""",
                (run_id,),
            )
        else:
            cursor = conn.execute(
                """INSERT INTO legacy_reuse_runs
                   (source_fingerprint, start_year, end_year, status)
                   VALUES (?, ?, ?, 'running')""",
                (fingerprint, start_year, end_year),
            )
            run_id = int(cursor.lastrowid)

    try:
        stats = _populate_inventory(
            db_path,
            run_id,
            universe,
            timings,
            focus,
            start_year,
            end_year,
        )
        with connect_rw(db_path) as conn:
            conn.execute(
                """UPDATE legacy_reuse_runs
                   SET status = 'completed', universe_count = ?,
                       stats_json = ?, completed_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (
                    len(universe),
                    json.dumps(stats, sort_keys=True, separators=(",", ":")),
                    run_id,
                ),
            )
    except Exception as exc:
        with connect_rw(db_path) as conn:
            conn.execute(
                """UPDATE legacy_reuse_runs
                   SET status = 'failed', error = ?,
                       completed_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (str(exc)[:1000], run_id),
            )
        raise
    return _result_from_stats(run_id, False, fingerprint, stats)


def _populate_inventory(
    db_path: Path,
    run_id: int,
    universe: list[str],
    timings: dict[int, dict[str, str]],
    focus: set[str],
    start_year: int,
    end_year: int,
) -> dict[str, int]:
    financial_ok = financial_missing = 0
    cycle_ok = cycle_missing = 0
    with connect(db_path) as conn:
        symbol_rows = _symbol_baselines(
            conn, universe, start_year, end_year
        )
        cycle_rows = _cycle_baselines(conn, universe, timings)

    inventory_values = []
    queue_values = []
    for row in symbol_rows:
        symbol = str(row["symbol"])
        quarterly = int(row.get("quarterly_rows") or 0)
        missing_quarters = _missing_quarter_count(
            row.get("financial_start"),
            quarterly,
            end_year,
        )
        if not quarterly:
            status = "baseline_missing"
            financial_missing += 1
        elif missing_quarters:
            status = "baseline_partial_unverified"
            financial_ok += 1
        else:
            status = "baseline_complete_unverified"
            financial_ok += 1
        details = {
            "source": "existing_vietnam_stocks_db",
            "authority": "vendor_or_legacy_only",
            "strict_pit_eligible": False,
            "financial_start": row.get("financial_start"),
            "financial_end": row.get("financial_end"),
        }
        digest = _hash_payload({**row, "details": details})
        inventory_values.append(
            (
                run_id,
                symbol,
                row.get("exchange"),
                row.get("listed_date"),
                row.get("first_price_date"),
                row.get("last_price_date"),
                int(row.get("price_rows") or 0),
                quarterly,
                int(row.get("vendor_dated_rows") or 0),
                missing_quarters,
                int(row.get("event_rows") or 0),
                int(row.get("adjusted_price_rows") or 0),
                status,
                digest,
                json.dumps(details, sort_keys=True, separators=(",", ":")),
            )
        )
        priority = 10 if symbol in focus else 30
        missing = ["official_document", "document_sha256"]
        if not quarterly:
            missing.append("quarterly_eps")
        if int(row.get("vendor_dated_rows") or 0) < quarterly:
            missing.append("official_available_at")
        queue_values.extend(
            [
                _queue_value(
                    run_id,
                    symbol,
                    "financial_filing",
                    f"{start_year - 5}Q1-{end_year}Q2",
                    priority,
                    status,
                    digest,
                    missing,
                ),
                _queue_value(
                    run_id,
                    symbol,
                    "shares_outstanding",
                    f"{start_year}-{end_year}",
                    priority + 1,
                    (
                        "current_only_unverified"
                        if row.get("current_issue_share")
                        else "baseline_missing"
                    ),
                    digest,
                    [
                        "effective_date_history",
                        "official_document",
                        "document_sha256",
                    ],
                ),
                _queue_value(
                    run_id,
                    symbol,
                    "corporate_action",
                    f"{start_year}-{end_year + 1}",
                    priority + 2,
                    (
                        "vendor_events_unverified"
                        if int(row.get("event_rows") or 0)
                        else "baseline_missing"
                    ),
                    digest,
                    [
                        "official_action_coverage",
                        "official_document",
                        "document_sha256",
                    ],
                ),
            ]
        )

    cycle_values = []
    for row in cycle_rows:
        complete = (
            row.get("signal_close_legacy") is not None
            and row.get("execution_open_legacy") is not None
            and int(row.get("adv_sessions") or 0) >= 20
        )
        status = (
            "baseline_complete_unverified"
            if complete
            else "baseline_incomplete"
        )
        cycle_ok += int(complete)
        cycle_missing += int(not complete)
        details = {
            "raw_unit_assumption": "THOUSAND_VND",
            "price_basis": "legacy_unknown",
            "strict_pit_eligible": False,
        }
        digest = _hash_payload({**row, "details": details})
        cycle_values.append(
            (
                run_id,
                row["symbol"],
                row["cycle_year"],
                row["signal_date"],
                row["execution_date"],
                row.get("signal_close_legacy"),
                row.get("execution_open_legacy"),
                int(row.get("adv_sessions") or 0),
                status,
                digest,
                json.dumps(details, sort_keys=True, separators=(",", ":")),
            )
        )
        missing = [
            "source_url",
            "payload_sha256",
            "verified_unadjusted_basis",
        ]
        if row.get("signal_close_legacy") is None:
            missing.append("signal_close")
        if row.get("execution_open_legacy") is None:
            missing.append("execution_open")
        if int(row.get("adv_sessions") or 0) < 20:
            missing.append("adv_20_sessions")
        queue_values.append(
            _queue_value(
                run_id,
                str(row["symbol"]),
                "market_price",
                str(row["cycle_year"]),
                10 if str(row["symbol"]) in focus else 30,
                status,
                digest,
                missing,
            )
        )

    benchmark_hash = _hash_payload(timings)
    for year, timing in timings.items():
        queue_values.append(
            _queue_value(
                run_id,
                "VNINDEX",
                "benchmark_total_return",
                str(year),
                5,
                "price_index_only_unverified",
                benchmark_hash,
                [
                    "verified_total_return_series",
                    "source_url",
                    "document_sha256",
                ],
            )
        )

    with connect_rw(db_path) as conn:
        conn.executemany(
            """INSERT INTO legacy_symbol_inventory
               (run_id, symbol, exchange, listed_date, first_price_date,
                last_price_date, price_rows, quarterly_rows,
                vendor_dated_rows, missing_quarter_count, event_rows,
                adjusted_price_rows, baseline_status, baseline_sha256,
                details_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            inventory_values,
        )
        conn.executemany(
            """INSERT INTO legacy_cycle_inventory
               (run_id, symbol, cycle_year, signal_date, execution_date,
                signal_close_legacy, execution_open_legacy, adv_sessions,
                baseline_status, baseline_sha256, details_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            cycle_values,
        )
        conn.executemany(
            """INSERT INTO legacy_verification_queue
               (run_id, symbol, evidence_type, period_key, priority,
                baseline_status, baseline_sha256, missing_fields_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            queue_values,
        )
    return {
        "universe_count": len(universe),
        "financial_baseline_symbols": financial_ok,
        "financial_missing_symbols": financial_missing,
        "cycle_price_complete": cycle_ok,
        "cycle_price_incomplete": cycle_missing,
        "queue_items": len(queue_values),
    }


def _symbol_baselines(
    conn, universe: list[str], start_year: int, end_year: int
) -> list[dict[str, Any]]:
    if not universe:
        return []
    placeholders = ",".join("?" for _ in universe)
    return fetch_all(
        conn,
        f"""WITH financial AS (
                SELECT symbol,
                       COUNT(*) AS quarterly_rows,
                       SUM(CASE WHEN available_at IS NOT NULL
                                THEN 1 ELSE 0 END) AS vendor_dated_rows,
                       MIN(year || '-Q' || quarter) AS financial_start,
                       MAX(year || '-Q' || quarter) AS financial_end
                FROM financial_ratios
                WHERE quarter BETWEEN 1 AND 4
                  AND year BETWEEN ? AND ?
                  AND (year < ? OR quarter <= 2)
                GROUP BY symbol
            ),
            prices AS (
                SELECT symbol, MIN(time) AS first_price_date,
                       MAX(time) AS last_price_date, COUNT(*) AS price_rows
                FROM stock_price_history GROUP BY symbol
            ),
            event_counts AS (
                SELECT symbol, COUNT(*) AS event_rows
                FROM events GROUP BY symbol
            ),
            adjusted AS (
                SELECT symbol, COUNT(*) AS adjusted_price_rows
                FROM adjusted_price_history GROUP BY symbol
            ),
            exchange_one AS (
                SELECT ticker, MIN(exchange) AS exchange
                FROM stock_exchange GROUP BY ticker
            )
            SELECT s.ticker AS symbol, e.exchange, s.listed_date,
                   p.first_price_date, p.last_price_date,
                   COALESCE(p.price_rows, 0) AS price_rows,
                   COALESCE(f.quarterly_rows, 0) AS quarterly_rows,
                   COALESCE(f.vendor_dated_rows, 0) AS vendor_dated_rows,
                   COALESCE(v.event_rows, 0) AS event_rows,
                   COALESCE(a.adjusted_price_rows, 0)
                       AS adjusted_price_rows,
                   f.financial_start, f.financial_end,
                   c.issue_share AS current_issue_share
            FROM stocks s
            LEFT JOIN exchange_one e ON e.ticker = s.ticker
            LEFT JOIN prices p ON p.symbol = s.ticker
            LEFT JOIN financial f ON f.symbol = s.ticker
            LEFT JOIN event_counts v ON v.symbol = s.ticker
            LEFT JOIN adjusted a ON a.symbol = s.ticker
            LEFT JOIN company_overview c ON c.symbol = s.ticker
            WHERE s.ticker IN ({placeholders})
            ORDER BY s.ticker""",
        (
            start_year - 5,
            end_year,
            end_year,
            *universe,
        ),
    )


def _cycle_baselines(
    conn, universe: list[str], timings: dict[int, dict[str, str]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year, timing in timings.items():
        signal_date = timing["signal_price_date"]
        execution_date = timing["execution_date"]
        adv_start = (
            dt.date.fromisoformat(execution_date) - dt.timedelta(days=45)
        ).isoformat()
        if not universe:
            continue
        placeholders = ",".join("?" for _ in universe)
        cycle = fetch_all(
            conn,
            f"""SELECT symbols.symbol, ? AS cycle_year,
                       ? AS signal_date, ? AS execution_date,
                       signal.close AS signal_close_legacy,
                       execution.open AS execution_open_legacy,
                       COALESCE(adv.sessions, 0) AS adv_sessions
                FROM (
                    SELECT ticker AS symbol FROM stocks
                    WHERE ticker IN ({placeholders})
                ) symbols
                LEFT JOIN stock_price_history signal
                  ON signal.symbol = symbols.symbol AND signal.time = ?
                LEFT JOIN stock_price_history execution
                  ON execution.symbol = symbols.symbol
                 AND execution.time = ?
                LEFT JOIN (
                    SELECT symbol, COUNT(*) AS sessions
                    FROM (
                        SELECT symbol, time,
                               ROW_NUMBER() OVER (
                                   PARTITION BY symbol ORDER BY time DESC
                               ) AS seq
                        FROM stock_price_history
                        WHERE time < ? AND time >= ?
                          AND volume IS NOT NULL
                          AND symbol IN ({placeholders})
                    )
                    WHERE seq <= 20
                    GROUP BY symbol
                ) adv ON adv.symbol = symbols.symbol
                WHERE EXISTS (
                    SELECT 1 FROM stock_price_history active
                    WHERE active.symbol = symbols.symbol
                      AND active.time <= ?
                )
                ORDER BY symbols.symbol""",
            (
                year,
                signal_date,
                execution_date,
                *universe,
                signal_date,
                execution_date,
                execution_date,
                adv_start,
                *universe,
                execution_date,
            ),
        )
        rows.extend(cycle)
    return rows


def _stored_focus_symbols(
    db_path: Path, start_year: int, end_year: int
) -> set[str]:
    with connect(db_path) as conn:
        rows = fetch_all(
            conn,
            """SELECT DISTINCT symbol FROM (
                   SELECT symbol FROM fund_holdings
                   UNION ALL
                   SELECT i.symbol
                   FROM strategy_cycle_snapshot_items i
                   JOIN strategy_cycle_snapshots c
                     ON c.id = i.cycle_snapshot_id
                   WHERE c.hold_year BETWEEN ? AND ?
               )""",
            (start_year, end_year),
        )
    return {str(row["symbol"]) for row in rows}


def _source_fingerprint(
    db_path: Path,
    start_year: int,
    end_year: int,
    universe: list[str],
    timings: dict[int, dict[str, str]],
    focus: set[str],
) -> str:
    with connect(db_path) as conn:
        payload = {
            "years": [start_year, end_year],
            "universe_sha": hashlib.sha256(
                "\n".join(universe).encode("utf-8")
            ).hexdigest(),
            "focus_sha": hashlib.sha256(
                "\n".join(sorted(focus)).encode("utf-8")
            ).hexdigest(),
            "timings": timings,
            "prices": fetch_one(
                conn,
                """SELECT COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols,
                          MIN(time) AS first_date, MAX(time) AS last_date,
                          ROUND(SUM(COALESCE(close, 0)), 3) AS close_sum
                   FROM stock_price_history""",
            ),
            "financials": fetch_one(
                conn,
                """SELECT COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols,
                          MAX(updated_at) AS last_update,
                          ROUND(SUM(COALESCE(eps_vnd, 0)), 3) AS eps_sum
                   FROM financial_ratios""",
            ),
            "events": fetch_one(
                conn,
                """SELECT COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols,
                          MAX(created_at) AS last_update
                   FROM events""",
            ),
            "adjusted": fetch_one(
                conn,
                """SELECT COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols,
                          MIN(price_date) AS first_date,
                          MAX(price_date) AS last_date,
                          MAX(fetched_at) AS last_update
                   FROM adjusted_price_history""",
            ),
        }
    return _hash_payload(payload)


def _missing_quarter_count(
    financial_start: str | None,
    actual_rows: int,
    end_year: int,
) -> int:
    """Count internal quarter gaps up to Q2 of a September strategy year."""
    if not financial_start or actual_rows <= 0:
        return 0
    try:
        year_text, quarter_text = financial_start.split("-Q", 1)
        start_index = int(year_text) * 4 + int(quarter_text) - 1
    except (TypeError, ValueError):
        return 0
    end_index = end_year * 4 + 1
    return max(0, end_index - start_index + 1 - actual_rows)


def _financial_rows_hash(rows: list[dict[str, Any]]) -> str:
    fields = (
        "symbol",
        "period",
        "year",
        "quarter",
        "price_to_book",
        "price_to_earnings",
        "eps_vnd",
        "bvps_vnd",
        "roe",
        "market_cap_billions",
        "shares_outstanding_millions",
        "public_date",
        "source_created_at",
        "source_updated_at",
        "available_at",
        "publication_status",
    )
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            json.dumps(
                [row.get(field) for field in fields],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _queue_value(
    run_id: int,
    symbol: str,
    evidence_type: str,
    period_key: str,
    priority: int,
    baseline_status: str,
    digest: str,
    missing: list[str],
) -> tuple[Any, ...]:
    return (
        run_id,
        symbol,
        evidence_type,
        period_key,
        priority,
        baseline_status,
        digest,
        json.dumps(sorted(set(missing)), separators=(",", ":")),
    )


def _result_from_stats(
    run_id: int,
    reused: bool,
    fingerprint: str,
    stats: dict[str, int],
) -> LegacyReuseResult:
    return LegacyReuseResult(
        run_id=run_id,
        reused_existing_run=reused,
        universe_count=int(stats["universe_count"]),
        financial_baseline_symbols=int(
            stats["financial_baseline_symbols"]
        ),
        financial_missing_symbols=int(stats["financial_missing_symbols"]),
        cycle_price_complete=int(stats["cycle_price_complete"]),
        cycle_price_incomplete=int(stats["cycle_price_incomplete"]),
        queue_items=int(stats["queue_items"]),
        source_fingerprint=fingerprint,
    )
