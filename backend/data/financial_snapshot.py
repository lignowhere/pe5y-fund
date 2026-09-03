"""Atomic, single-source financial data refreshes.

Rows are downloaded into a run-scoped staging table.  The live
``financial_ratios`` table is replaced in one SQLite transaction only after
every requested symbol has been processed and coverage checks pass.  A
cancelled or interrupted refresh therefore leaves the active dataset intact.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ..database.connection import connect, connect_rw, fetch_all, fetch_one
from .vci_client import VCIClient, VCIFinancialRow

SOURCE = "VCI"
SOURCE_API = "vietcap_legacy_archive_pre2018+iq_publication_v2"
IQ_HISTORY_START_YEAR = 2018
POINT_IN_TIME_METHODOLOGY = "official_revision_pit_v2"
VENDOR_RESEARCH_METHODOLOGY = "vendor_publication_research_v2"


class FinancialSnapshotError(RuntimeError):
    """Raised when a staged dataset is incomplete or unsafe to activate."""


@dataclass(frozen=True)
class FinancialSnapshotProgress:
    symbol: str
    index: int
    total: int
    status: str
    rows_staged: int
    staged_so_far: int
    failed_so_far: int
    error: str | None = None


@dataclass(frozen=True)
class FinancialSnapshotResult:
    version_id: int
    content_hash: str
    row_count: int
    symbol_count: int
    empty_symbol_count: int
    verified_row_count: int
    publication_coverage_pct: float


def capture_vendor_research_symbol(
    db_path: Path,
    symbol: str,
    vci: VCIClient,
) -> dict[str, int | str]:
    """Persist one vendor symbol as quarantined research evidence.

    This is intentionally non-active and never updates ``financial_ratios``.
    It is useful for cases such as DP3 where the vendor can recover missing
    rows while official filing documents are still awaiting verification.
    """
    normalized = symbol.strip().upper()
    rows = vci.get_all_financial_ratios(normalized)
    if not rows:
        raise FinancialSnapshotError(
            f"Vendor returned no financial rows for {normalized}"
        )
    values = [_row_values(0, normalized, row)[1:] for row in rows]
    digest = hashlib.sha256()
    for value in sorted(values, key=lambda item: (item[2], item[3] or 0)):
        digest.update(
            json.dumps(
                value,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    content_hash = digest.hexdigest()
    latest_year = max(int(value[2]) for value in values)
    latest_quarter = max(
        (
            int(value[3])
            for value in values
            if int(value[2]) == latest_year and value[3] is not None
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
                   VALUES (?, ?, ?, ?, ?, ?, 1, 0, 0, 0, 0, ?, 0,
                           'quarantined_vendor_research', ?)""",
                (
                    SOURCE,
                    SOURCE_API,
                    latest_year,
                    latest_quarter,
                    content_hash,
                    len(values),
                    VENDOR_RESEARCH_METHODOLOGY,
                    json.dumps(
                        [
                            "OFFICIAL_DOCUMENT_REQUIRED",
                            f"SYMBOL:{normalized}",
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
                [(version_id, *value) for value in values],
            )
    return {
        "symbol": normalized,
        "version_id": version_id,
        "row_count": len(values),
        "content_hash": content_hash,
        "status": "quarantined_vendor_research",
    }


def financial_universe(db_path: Path) -> list[str]:
    """Return every exchange ticker plus symbols already present historically."""
    with connect(db_path) as conn:
        rows = fetch_all(
            conn,
            """SELECT symbol FROM (
                   SELECT DISTINCT UPPER(s.ticker) AS symbol
                   FROM stocks s
                   JOIN stock_exchange se ON se.ticker = s.ticker
                   WHERE LENGTH(s.ticker) = 3
                     AND s.ticker GLOB '[A-Z][A-Z][A-Z]'
                     AND se.exchange IN ('HSX', 'HNX', 'UPCOM')
                   UNION
                   SELECT DISTINCT UPPER(symbol) AS symbol
                   FROM financial_ratios
                   WHERE LENGTH(symbol) = 3
                     AND symbol GLOB '[A-Z][A-Z][A-Z]'
               )
               ORDER BY symbol""",
        )
    return [row["symbol"] for row in rows]


def get_active_financial_version(db_path: Path) -> dict | None:
    with connect(db_path) as conn:
        return fetch_one(
            conn,
            """SELECT * FROM financial_data_versions
               WHERE is_active = 1 ORDER BY id DESC LIMIT 1""",
        )


def stage_vci_financials(
    db_path: Path,
    run_id: int,
    symbols: Iterable[str],
    vci: VCIClient,
    *,
    on_progress: Callable[[FinancialSnapshotProgress], None] | None = None,
) -> dict[str, int]:
    """Fetch all symbols from Vietcap IQ without mutating the live ratio table."""
    normalized = sorted({symbol.strip().upper() for symbol in symbols if symbol})
    required_symbols = _required_investment_symbols(db_path)
    with connect_rw(db_path) as conn:
        conn.execute(
            "DELETE FROM financial_ratios_staging WHERE run_id = ?", (run_id,)
        )
        conn.execute(
            "DELETE FROM financial_sync_symbols WHERE run_id = ?", (run_id,)
        )
        # Vietcap IQ currently exposes statements from 2018 onward. Preserve
        # the non-overlapping Vietcap archive before that boundary so older
        # backtest windows remain available, while every overlapping year is
        # refreshed uniformly from IQ.
        conn.execute(
            """INSERT INTO financial_ratios_staging
               (run_id, symbol, period, year, quarter,
                price_to_book, price_to_earnings, eps_vnd,
                bvps_vnd, roe, market_cap_billions,
                shares_outstanding_millions, data_json, source,
                public_date, source_created_at, source_updated_at,
                available_at, publication_status)
               SELECT ?, symbol, period, year, quarter,
                      price_to_book, price_to_earnings, eps_vnd,
                      bvps_vnd, roe, market_cap_billions,
                      shares_outstanding_millions, data_json, ?,
                      public_date, source_created_at, source_updated_at,
                      NULL, 'legacy_unverified'
               FROM financial_ratios
               WHERE year < ? AND UPPER(COALESCE(source, 'VCI')) = 'VCI'""",
            (run_id, SOURCE, IQ_HISTORY_START_YEAR),
        )

    staged = failed = empty = 0
    for index, symbol in enumerate(normalized):
        try:
            rows = vci.get_all_financial_ratios(symbol)
            values = [_row_values(run_id, symbol, row) for row in rows]
            with connect_rw(db_path) as conn:
                if values:
                    conn.executemany(
                        """INSERT OR REPLACE INTO financial_ratios_staging
                           (run_id, symbol, period, year, quarter,
                            price_to_book, price_to_earnings, eps_vnd,
                            bvps_vnd, roe, market_cap_billions,
                            shares_outstanding_millions, data_json, source,
                            public_date, source_created_at, source_updated_at,
                            available_at, publication_status)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        values,
                    )
                status = "ok" if values else "empty"
                conn.execute(
                    """INSERT OR REPLACE INTO financial_sync_symbols
                       (run_id, symbol, status, row_count, error, updated_at)
                       VALUES (?, ?, ?, ?, NULL, CURRENT_TIMESTAMP)""",
                    (run_id, symbol, status, len(values)),
                )
                conn.execute(
                    """INSERT OR REPLACE INTO financial_sync_symbol_history
                       (run_id, symbol, status, row_count,
                        required_for_investment, error, observed_at)
                       VALUES (?, ?, ?, ?, ?, NULL, CURRENT_TIMESTAMP)""",
                    (
                        run_id,
                        symbol,
                        "verified" if values else "source_empty",
                        len(values),
                        int(symbol in required_symbols),
                    ),
                )
            staged += len(values)
            if not values:
                empty += 1
            progress = FinancialSnapshotProgress(
                symbol=symbol,
                index=index,
                total=len(normalized),
                status=status,
                rows_staged=len(values),
                staged_so_far=staged,
                failed_so_far=failed,
            )
        except Exception as exc:
            failed += 1
            message = str(exc)[:500]
            with connect_rw(db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO financial_sync_symbols
                       (run_id, symbol, status, row_count, error, updated_at)
                       VALUES (?, ?, 'error', 0, ?, CURRENT_TIMESTAMP)""",
                    (run_id, symbol, message),
                )
                conn.execute(
                    """INSERT OR REPLACE INTO financial_sync_symbol_history
                       (run_id, symbol, status, row_count,
                        required_for_investment, error, observed_at)
                       VALUES (?, ?, 'error', 0, ?, ?, CURRENT_TIMESTAMP)""",
                    (
                        run_id,
                        symbol,
                        int(symbol in required_symbols),
                        message,
                    ),
                )
            progress = FinancialSnapshotProgress(
                symbol=symbol,
                index=index,
                total=len(normalized),
                status="error",
                rows_staged=0,
                staged_so_far=staged,
                failed_so_far=failed,
                error=message,
            )
        if on_progress is not None:
            on_progress(progress)

    return {
        "total": len(normalized),
        "rows_staged": staged,
        "failed": failed,
        "empty": empty,
    }


def activate_staged_financials(
    db_path: Path,
    run_id: int,
    *,
    as_of_year: int,
    as_of_quarter: int,
    expected_symbols: int,
    min_existing_coverage: float = 0.80,
) -> FinancialSnapshotResult:
    """Atomically promote a complete staged run and return its version."""
    with connect(db_path) as conn:
        status = fetch_one(
            conn,
            """SELECT COUNT(*) AS processed,
                      SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS failed,
                      SUM(CASE WHEN status = 'empty' THEN 1 ELSE 0 END) AS empty
               FROM financial_sync_symbols WHERE run_id = ?""",
            (run_id,),
        ) or {}
        counts = fetch_one(
            conn,
            """SELECT COUNT(*) AS rows,
                      COUNT(DISTINCT symbol) AS symbols,
                      COUNT(DISTINCT CASE WHEN quarter IS NOT NULL
                                          THEN symbol END) AS quarterly_symbols,
                      SUM(CASE WHEN year >= ?
                                THEN 1 ELSE 0 END) AS iq_rows,
                      SUM(CASE WHEN year >= ?
                                AND publication_status = 'verified'
                                AND available_at IS NOT NULL
                               THEN 1 ELSE 0 END) AS verified_rows
               FROM financial_ratios_staging WHERE run_id = ?""",
            (IQ_HISTORY_START_YEAR, IQ_HISTORY_START_YEAR, run_id),
        ) or {}
        existing = fetch_one(
            conn,
            """SELECT COUNT(*) AS rows,
                      COUNT(DISTINCT symbol) AS symbols,
                      COUNT(DISTINCT CASE WHEN quarter IS NOT NULL
                                          THEN symbol END) AS quarterly_symbols
               FROM financial_ratios""",
        ) or {}

    processed = int(status.get("processed") or 0)
    failed = int(status.get("failed") or 0)
    if processed != expected_symbols:
        raise FinancialSnapshotError(
            f"Financial staging incomplete: {processed}/{expected_symbols} symbols"
        )
    if failed:
        raise FinancialSnapshotError(
            f"Financial staging has {failed} failed symbols; live data was not changed"
        )
    row_count = int(counts.get("rows") or 0)
    symbol_count = int(counts.get("symbols") or 0)
    if row_count == 0 or symbol_count == 0:
        raise FinancialSnapshotError("Vietcap staging returned no financial data")

    existing_rows = int(existing.get("rows") or 0)
    existing_quarterly_symbols = int(existing.get("quarterly_symbols") or 0)
    staged_quarterly_symbols = int(counts.get("quarterly_symbols") or 0)
    if existing_rows >= 100 and row_count < existing_rows * min_existing_coverage:
        raise FinancialSnapshotError(
            "Staged financial row coverage is unexpectedly below the active dataset"
        )
    if (
        existing_quarterly_symbols >= 20
        and staged_quarterly_symbols
        < existing_quarterly_symbols * min_existing_coverage
    ):
        raise FinancialSnapshotError(
            "Staged quarterly-symbol coverage is unexpectedly below the active dataset"
        )
    iq_rows = int(counts.get("iq_rows") or 0)
    verified_rows = int(counts.get("verified_rows") or 0)
    publication_coverage = verified_rows / iq_rows if iq_rows else 0.0
    if iq_rows == 0 or publication_coverage < 0.90:
        raise FinancialSnapshotError(
            "Vietcap publication-date coverage is below the required 90% "
            f"({publication_coverage * 100:.2f}%). Live data was not changed."
        )

    content_hash = _staged_content_hash(db_path, run_id)
    empty_count = int(status.get("empty") or 0)
    required_symbols = _required_investment_symbols(db_path)
    with connect(db_path) as conn:
        classified_symbols = {
            str(row["symbol"])
            for row in fetch_all(
                conn,
                """SELECT symbol FROM financial_sync_symbol_history
                   WHERE run_id = ?""",
                (run_id,),
            )
        }
    missing_classification = sorted(required_symbols - classified_symbols)
    if missing_classification:
        with connect_rw(db_path) as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO financial_sync_symbol_history
                   (run_id, symbol, status, row_count,
                    required_for_investment, error, observed_at)
                   VALUES (?, ?, 'ingestion_missing', 0, 1, ?,
                           CURRENT_TIMESTAMP)""",
                [
                    (
                        run_id,
                        symbol,
                        "Required symbol was not included in the staged run",
                    )
                    for symbol in missing_classification
                ],
            )
        raise FinancialSnapshotError(
            "Required investment-universe symbols were not classified: "
            + ", ".join(missing_classification[:30])
        )

    with connect(db_path) as conn:
        required_failures = fetch_all(
            conn,
            """SELECT symbol, status
               FROM financial_sync_symbol_history
               WHERE run_id = ?
                 AND required_for_investment = 1
                 AND status NOT IN ('verified', 'not_published',
                                    'not_applicable')
               ORDER BY symbol""",
            (run_id,),
        )
    if required_failures:
        details = ", ".join(
            f"{row['symbol']}:{row['status']}"
            for row in required_failures[:30]
        )
        raise FinancialSnapshotError(
            "Required investment-universe symbols are unresolved: "
            + details
        )

    # One writer transaction: readers see either the complete old dataset or
    # the complete new dataset, never a per-symbol mixture.
    with connect_rw(db_path) as conn:
        existing_version = fetch_one(
            conn,
            "SELECT id FROM financial_data_versions WHERE content_hash = ?",
            (content_hash,),
        )
        if existing_version:
            version_id = int(existing_version["id"])
        else:
            cur = conn.execute(
                """INSERT INTO financial_data_versions
                   (source, source_api, as_of_year, as_of_quarter,
                    content_hash, row_count, symbol_count, sync_run_id,
                    point_in_time_ready, publication_coverage_pct,
                    verified_row_count, methodology_version,
                    official_provenance_ready, quality_status,
                    quality_issues_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 0,
                           'vendor_research',
                           '["OFFICIAL_DOCUMENT_REQUIRED"]')""",
                (
                    SOURCE,
                    SOURCE_API,
                    as_of_year,
                    as_of_quarter,
                    content_hash,
                    row_count,
                    symbol_count,
                    run_id,
                    publication_coverage * 100.0,
                    verified_rows,
                    VENDOR_RESEARCH_METHODOLOGY,
                ),
            )
            version_id = int(cur.lastrowid)

        conn.execute(
            """INSERT OR IGNORE INTO financial_ratio_versions
               (financial_data_version_id, symbol, period, year, quarter,
                price_to_book, price_to_earnings, eps_vnd,
                bvps_vnd, roe, market_cap_billions,
                shares_outstanding_millions, data_json, source,
                public_date, source_created_at, source_updated_at,
                available_at, publication_status)
               SELECT ?, symbol, period, year, quarter,
                      price_to_book, price_to_earnings, eps_vnd,
                      bvps_vnd, roe, market_cap_billions,
                      shares_outstanding_millions, data_json, source,
                      public_date, source_created_at, source_updated_at,
                      available_at, publication_status
               FROM financial_ratios_staging
               WHERE run_id = ?
               ORDER BY symbol, year, COALESCE(quarter, 0)""",
            (version_id, run_id),
        )
        conn.execute("DELETE FROM financial_ratios")
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
               FROM financial_ratios_staging
               WHERE run_id = ?
               ORDER BY symbol, year, COALESCE(quarter, 0)""",
            (version_id, run_id),
        )
        conn.execute(
            "UPDATE financial_data_versions SET is_active = 0 WHERE is_active = 1"
        )
        conn.execute(
            "UPDATE financial_data_versions SET is_active = 1 WHERE id = ?",
            (version_id,),
        )
        conn.execute(
            """UPDATE data_sync_runs
               SET financial_version_id = ?, financial_rows_staged = ?
               WHERE id = ?""",
            (version_id, row_count, run_id),
        )
        conn.execute(
            "DELETE FROM financial_ratios_staging WHERE run_id = ?", (run_id,)
        )

    return FinancialSnapshotResult(
        version_id=version_id,
        content_hash=content_hash,
        row_count=row_count,
        symbol_count=symbol_count,
        empty_symbol_count=empty_count,
        verified_row_count=verified_rows,
        publication_coverage_pct=round(publication_coverage * 100.0, 4),
    )


def _required_investment_symbols(db_path: Path) -> set[str]:
    """Listed symbols with recent prices must never disappear silently."""
    with connect(db_path) as conn:
        required_tables = {
            row["name"]
            for row in fetch_all(
                conn,
                """SELECT name FROM sqlite_master
                   WHERE type = 'table'
                     AND name IN (
                        'stocks', 'stock_exchange', 'stock_price_history'
                     )""",
            )
        }
        if required_tables != {
            "stocks", "stock_exchange", "stock_price_history"
        }:
            return set()
        stock_columns = {
            row["name"] for row in fetch_all(conn, "PRAGMA table_info(stocks)")
        }
        price_columns = {
            row["name"]
            for row in fetch_all(
                conn, "PRAGMA table_info(stock_price_history)"
            )
        }
        if "status" not in stock_columns or not {
            "symbol", "time", "close", "volume"
        }.issubset(price_columns):
            return set()
        rows = fetch_all(
            conn,
            """WITH latest AS (
                   SELECT MAX(time) AS price_date
                   FROM stock_price_history
                   WHERE typeof(time) = 'text'
               )
               SELECT DISTINCT UPPER(s.ticker) AS symbol
               FROM stocks s
               JOIN stock_exchange e ON e.ticker = s.ticker
               JOIN stock_price_history p ON p.symbol = s.ticker
               CROSS JOIN latest
               WHERE e.exchange IN ('HSX', 'HNX', 'UPCOM')
                 AND COALESCE(LOWER(s.status), 'listed') = 'listed'
                 AND p.time >= date(latest.price_date, '-365 days')
                 AND p.close IS NOT NULL
                 AND p.volume > 0""",
        )
    return {str(row["symbol"]) for row in rows}


def _row_values(
    run_id: int, symbol: str, row: VCIFinancialRow
) -> tuple:
    period = (
        str(row.year)
        if row.quarter is None
        else f"{row.year}-Q{row.quarter}"
    )
    pb = _float(row.pb)
    pe = _float(row.pe)
    eps = _float(row.eps)
    bvps = _float(row.bvps)
    roe = _float(row.roe)
    market_cap = _float(row.ev)
    shares = _float(row.issue_share)
    public_date = _date(getattr(row, "public_date", None))
    source_created_at = _timestamp(
        getattr(row, "source_created_at", None)
    )
    source_updated_at = _timestamp(
        getattr(row, "source_updated_at", None)
    )
    available_at = _available_at(
        public_date, source_created_at, source_updated_at
    )
    publication_status = (
        "verified" if public_date and available_at else "missing_public_date"
    )
    data_json = json.dumps(
        {
            key: value for key, value in {
                "price_to_book": pb,
                "price_to_earnings": pe,
                "eps_vnd": eps,
                "bvps_vnd": bvps,
                "roe": roe,
                "market_cap_billions": market_cap,
                "shares_outstanding_millions": shares,
                "year": row.year,
                "quarter": row.quarter,
                "period": period,
                "public_date": public_date,
                "source_created_at": source_created_at,
                "source_updated_at": source_updated_at,
                "available_at": available_at,
                "publication_status": publication_status,
            }.items()
            if value is not None
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        run_id,
        symbol,
        period,
        row.year,
        row.quarter,
        pb,
        pe,
        eps,
        bvps,
        roe,
        market_cap,
        shares,
        data_json,
        SOURCE,
        public_date,
        source_created_at,
        source_updated_at,
        available_at,
        publication_status,
    )


def _staged_content_hash(db_path: Path, run_id: int) -> str:
    digest = hashlib.sha256()
    with connect(db_path) as conn:
        cursor = conn.execute(
            """SELECT symbol, period, year, quarter, price_to_book,
                      price_to_earnings, eps_vnd, bvps_vnd, roe,
                      market_cap_billions, shares_outstanding_millions,
                      public_date, source_created_at, source_updated_at,
                      available_at, publication_status
               FROM financial_ratios_staging
               WHERE run_id = ?
               ORDER BY symbol, year, COALESCE(quarter, 0), period""",
            (run_id,),
        )
        for row in cursor:
            payload = json.dumps(
                list(row), ensure_ascii=True, separators=(",", ":")
            )
            digest.update(payload.encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def _float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value) -> str | None:
    if not value:
        return None
    text = str(value).strip().replace(" ", "T")
    return text or None


def _date(value) -> str | None:
    text = _timestamp(value)
    return text[:10] if text and len(text) >= 10 else None


def _available_at(
    public_date: str | None,
    source_created_at: str | None,
    source_updated_at: str | None,
) -> str | None:
    """Date on which this exact source revision could first be used."""
    if not public_date:
        return None
    candidates = [
        public_date,
        _date(source_created_at),
        _date(source_updated_at),
    ]
    return max(value for value in candidates if value)
