"""Append-only import helpers for official financial provenance."""
from __future__ import annotations

import hashlib
import json
import re
import datetime as dt
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..database.connection import connect, connect_rw, fetch_all, fetch_one

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OFFICIAL_AUTHORITIES = {"HSX", "HNX", "UPCOM", "ISSUER"}
_CANONICAL_PRICE_SOURCES = {"VCI", "HSX", "HNX", "UPCOM"}
_MARKET_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")


class ProvenanceError(ValueError):
    """Raised when evidence is incomplete or internally inconsistent."""


@dataclass(frozen=True)
class FilingEvidence:
    symbol: str
    year: int
    quarter: int
    statement_scope: str
    basic_eps_vnd: float
    published_at: str | None
    first_observed_at: str
    availability_basis: str
    source_authority: str
    source_url: str | None
    document_sha256: str
    content_sha256: str
    is_independent_quarter: bool = True


@dataclass(frozen=True)
class PriceEvidence:
    symbol: str
    price_date: str
    open_vnd: float
    high_vnd: float
    low_vnd: float
    close_vnd: float
    volume: float
    source: str
    source_url: str | None
    payload_sha256: str
    observed_at: str
    is_session_final: bool = True


@dataclass(frozen=True)
class CorporateActionEvidence:
    symbol: str
    action_type: str
    ex_date: str
    source_authority: str
    document_sha256: str
    observed_at: str
    source_url: str | None = None
    record_date: str | None = None
    payment_date: str | None = None
    cash_vnd_per_share: float | None = None
    share_factor: float | None = None
    subscription_price_vnd: float | None = None
    verification_status: str = "verified"


@dataclass(frozen=True)
class CorporateActionCoverageEvidence:
    symbol: str
    start_date: str
    end_date: str
    source_authority: str
    document_sha256: str
    observed_at: str


@dataclass(frozen=True)
class BenchmarkTotalReturnEvidence:
    symbol: str
    price_date: str
    index_value: float
    source_authority: str
    document_sha256: str
    observed_at: str
    source_url: str | None = None
    verification_status: str = "verified"


def import_filing_revision(
    db_path: Path,
    evidence: FilingEvidence,
    *,
    extractor_version: str = "official_manual_v1",
) -> int:
    """Insert one immutable filing revision and its extracted EPS fact."""
    symbol = evidence.symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{3,10}", symbol):
        raise ProvenanceError("Invalid symbol")
    if evidence.quarter not in {1, 2, 3, 4}:
        raise ProvenanceError("quarter must be 1..4")
    if evidence.statement_scope not in {"consolidated", "standalone"}:
        raise ProvenanceError("Invalid statement_scope")
    authority = evidence.source_authority.strip().upper()
    if authority not in _OFFICIAL_AUTHORITIES:
        raise ProvenanceError(
            "Strict PIT evidence must come from HSX/HNX/UPCOM/ISSUER"
        )
    _validate_hash(evidence.document_sha256, "document_sha256")
    _validate_hash(evidence.content_sha256, "content_sha256")
    available_at = _resolve_available_at(
        db_path,
        evidence.published_at,
        evidence.first_observed_at,
        evidence.availability_basis,
    )

    with connect_rw(db_path) as conn:
        existing = fetch_one(
            conn,
            """SELECT id FROM financial_filing_revisions
               WHERE symbol = ? AND year = ? AND quarter = ?
                 AND statement_scope = ? AND content_sha256 = ?""",
            (
                symbol,
                evidence.year,
                evidence.quarter,
                evidence.statement_scope,
                evidence.content_sha256,
            ),
        )
        if existing:
            return int(existing["id"])
        latest = fetch_one(
            conn,
            """SELECT id, revision_number, basic_eps_vnd, content_sha256
               FROM financial_filing_revisions
               WHERE symbol = ? AND year = ? AND quarter = ?
                 AND statement_scope = ?
               ORDER BY revision_number DESC, id DESC LIMIT 1""",
            (
                symbol,
                evidence.year,
                evidence.quarter,
                evidence.statement_scope,
            ),
        )
        revision_number = int((latest or {}).get("revision_number") or 0) + 1
        status = "verified"
        # Two different official documents claiming the same revision time are
        # not silently ranked; the conflict must be resolved explicitly.
        if (
            latest
            and float(latest["basic_eps_vnd"]) != float(evidence.basic_eps_vnd)
            and available_at == evidence.first_observed_at
            and evidence.availability_basis == "live_observed"
        ):
            status = "conflict"
        cur = conn.execute(
            """INSERT INTO financial_filing_revisions
               (symbol, year, quarter, statement_scope, revision_number,
                basic_eps_vnd, published_at, first_observed_at,
                available_at, availability_basis, source_authority,
                source_url, document_sha256, content_sha256,
                verification_status, supersedes_revision_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                symbol,
                evidence.year,
                evidence.quarter,
                evidence.statement_scope,
                revision_number,
                float(evidence.basic_eps_vnd),
                evidence.published_at,
                evidence.first_observed_at,
                available_at,
                evidence.availability_basis,
                authority,
                evidence.source_url,
                evidence.document_sha256,
                evidence.content_sha256,
                status,
                (latest or {}).get("id"),
            ),
        )
        revision_id = int(cur.lastrowid)
        conn.execute(
            """INSERT INTO financial_period_facts
               (filing_revision_id, symbol, year, quarter, basic_eps_vnd,
                is_independent_quarter, extractor_version)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                revision_id,
                symbol,
                evidence.year,
                evidence.quarter,
                float(evidence.basic_eps_vnd),
                int(evidence.is_independent_quarter),
                extractor_version,
            ),
        )
    return revision_id


def import_shares_outstanding(
    db_path: Path,
    *,
    symbol: str,
    effective_from: str,
    shares_outstanding: float,
    source_authority: str,
    document_sha256: str,
    observed_at: str,
    effective_to: str | None = None,
    source_url: str | None = None,
) -> int:
    authority = source_authority.strip().upper()
    if authority not in _OFFICIAL_AUTHORITIES:
        raise ProvenanceError("Shares evidence must use an official authority")
    _validate_hash(document_sha256, "document_sha256")
    if shares_outstanding <= 0:
        raise ProvenanceError("shares_outstanding must be positive")
    with connect_rw(db_path) as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO shares_outstanding_history
               (symbol, effective_from, effective_to, shares_outstanding,
                source_authority, source_url, document_sha256,
                verification_status, observed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'verified', ?)""",
            (
                symbol.strip().upper(),
                effective_from,
                effective_to,
                float(shares_outstanding),
                authority,
                source_url,
                document_sha256,
                observed_at,
            ),
        )
        if cur.lastrowid:
            return int(cur.lastrowid)
        row = fetch_one(
            conn,
            """SELECT id FROM shares_outstanding_history
               WHERE symbol = ? AND effective_from = ?
                 AND shares_outstanding = ? AND document_sha256 = ?""",
            (
                symbol.strip().upper(),
                effective_from,
                float(shares_outstanding),
                document_sha256,
            ),
        )
    return int(row["id"])


def import_price_observation(
    db_path: Path,
    evidence: PriceEvidence,
    *,
    db_scale_vnd: float = 1000.0,
) -> int:
    """Verify or insert one final unadjusted OHLCV observation."""
    symbol = evidence.symbol.strip().upper()
    source = evidence.source.strip().upper()
    if source not in _CANONICAL_PRICE_SOURCES:
        raise ProvenanceError("Unsupported canonical price source")
    _validate_hash(evidence.payload_sha256, "payload_sha256")
    if not evidence.is_session_final:
        raise ProvenanceError("Provisional price evidence cannot be verified")
    prices = [
        float(evidence.open_vnd),
        float(evidence.high_vnd),
        float(evidence.low_vnd),
        float(evidence.close_vnd),
    ]
    if min(prices) <= 0:
        raise ProvenanceError("OHLC prices must be positive")
    if prices[1] < max(prices[0], prices[3]) or prices[2] > min(
        prices[0], prices[3]
    ):
        raise ProvenanceError("OHLC values are internally inconsistent")
    if float(evidence.volume) < 0:
        raise ProvenanceError("volume must be non-negative")
    observed_at = _normalize_utc(evidence.observed_at)
    normalized = tuple(value / db_scale_vnd for value in prices)
    conflict_message: str | None = None
    observation_id = 0
    with connect_rw(db_path) as conn:
        existing = fetch_one(
            conn,
            """SELECT open, high, low, close, volume
               FROM stock_price_history
               WHERE symbol = ? AND time = ?""",
            (symbol, evidence.price_date),
        )
        metadata = fetch_one(
            conn,
            """SELECT source FROM market_price_metadata
               WHERE symbol = ? AND price_date = ?""",
            (symbol, evidence.price_date),
        )
        values_match = (
            existing is None
            or all(
                existing[field] is not None
                and abs(
                    float(existing[field]) - normalized[index]
                ) <= 1e-8
                for index, field in enumerate(("open", "high", "low", "close"))
            )
            and existing["volume"] is not None
            and abs(
                float(existing["volume"]) - float(evidence.volume)
            ) <= 1e-6
        )
        source_matches = (
            metadata is None or str(metadata["source"]).upper() == source
        )
        status = "verified" if values_match and source_matches else "conflict"
        cursor = conn.execute(
            """INSERT OR IGNORE INTO price_source_observations
               (symbol, price_date, open_vnd, high_vnd, low_vnd, close_vnd,
                volume, source, source_url, payload_sha256, observed_at,
                is_session_final, verification_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                symbol,
                evidence.price_date,
                *prices,
                float(evidence.volume),
                source,
                evidence.source_url,
                evidence.payload_sha256.lower(),
                observed_at,
                status,
            ),
        )
        if cursor.lastrowid:
            observation_id = int(cursor.lastrowid)
        else:
            row = fetch_one(
                conn,
                """SELECT id FROM price_source_observations
                   WHERE symbol = ? AND price_date = ? AND source = ?
                     AND payload_sha256 = ?""",
                (
                    symbol,
                    evidence.price_date,
                    source,
                    evidence.payload_sha256.lower(),
                ),
            )
            observation_id = int(row["id"])
        if status == "conflict":
            conflict_message = (
                f"Price evidence conflicts with canonical data for "
                f"{symbol}@{evidence.price_date}"
            )
        else:
            if existing is None:
                conn.execute(
                    """INSERT INTO stock_price_history
                       (symbol, time, open, high, low, close, volume)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        symbol,
                        evidence.price_date,
                        *normalized,
                        float(evidence.volume),
                    ),
                )
            conn.execute(
                """INSERT INTO market_price_metadata
                   (symbol, price_date, source, price_basis, raw_unit,
                    is_provisional, observed_at, source_url,
                    source_payload_sha256)
                   VALUES (?, ?, ?, 'execution_unadjusted',
                           'THOUSAND_VND', 0, ?, ?, ?)
                   ON CONFLICT(symbol, price_date) DO UPDATE SET
                     price_basis = excluded.price_basis,
                     raw_unit = excluded.raw_unit,
                     is_provisional = 0,
                     observed_at = excluded.observed_at,
                     source_url = excluded.source_url,
                     source_payload_sha256 =
                       excluded.source_payload_sha256""",
                (
                    symbol,
                    evidence.price_date,
                    source,
                    observed_at,
                    evidence.source_url,
                    evidence.payload_sha256.lower(),
                ),
            )
    if conflict_message:
        raise ProvenanceError(conflict_message)
    return observation_id


def import_corporate_action(
    db_path: Path,
    evidence: CorporateActionEvidence,
) -> int:
    authority = evidence.source_authority.strip().upper()
    if authority not in _OFFICIAL_AUTHORITIES:
        raise ProvenanceError("Corporate actions require an official source")
    _validate_hash(evidence.document_sha256, "document_sha256")
    action_type = evidence.action_type.strip().lower()
    allowed = {
        "cash_dividend",
        "stock_dividend",
        "split",
        "rights_issue",
        "other",
    }
    if action_type not in allowed:
        raise ProvenanceError("Unsupported action_type")
    status = evidence.verification_status.strip().lower()
    if status not in {"verified", "conflict", "unsupported"}:
        raise ProvenanceError("Invalid corporate-action status")
    if action_type == "cash_dividend" and (
        evidence.cash_vnd_per_share is None
        or evidence.cash_vnd_per_share < 0
        or not evidence.payment_date
    ):
        raise ProvenanceError(
            "Cash dividend requires value and payment_date"
        )
    if action_type in {"stock_dividend", "split"} and (
        evidence.share_factor is None or evidence.share_factor <= 0
    ):
        raise ProvenanceError("Share action requires a positive share_factor")
    with connect_rw(db_path) as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO corporate_actions
               (symbol, action_type, ex_date, record_date, payment_date,
                cash_vnd_per_share, share_factor, subscription_price_vnd,
                source_authority, source_url, document_sha256,
                verification_status, observed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                evidence.symbol.strip().upper(),
                action_type,
                evidence.ex_date,
                evidence.record_date,
                evidence.payment_date,
                evidence.cash_vnd_per_share,
                evidence.share_factor,
                evidence.subscription_price_vnd,
                authority,
                evidence.source_url,
                evidence.document_sha256.lower(),
                status,
                _normalize_utc(evidence.observed_at),
            ),
        )
        if cursor.lastrowid:
            return int(cursor.lastrowid)
        row = fetch_one(
            conn,
            """SELECT id FROM corporate_actions
               WHERE symbol = ? AND action_type = ? AND ex_date = ?
                 AND document_sha256 = ?""",
            (
                evidence.symbol.strip().upper(),
                action_type,
                evidence.ex_date,
                evidence.document_sha256.lower(),
            ),
        )
    return int(row["id"])


def import_corporate_action_coverage(
    db_path: Path,
    evidence: CorporateActionCoverageEvidence,
) -> int:
    authority = evidence.source_authority.strip().upper()
    if authority not in _OFFICIAL_AUTHORITIES:
        raise ProvenanceError("Coverage requires an official source")
    _validate_hash(evidence.document_sha256, "document_sha256")
    if evidence.end_date < evidence.start_date:
        raise ProvenanceError("Coverage end_date precedes start_date")
    with connect_rw(db_path) as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO corporate_action_coverage
               (symbol, start_date, end_date, coverage_status,
                source_authority, document_sha256, observed_at)
               VALUES (?, ?, ?, 'verified', ?, ?, ?)""",
            (
                evidence.symbol.strip().upper(),
                evidence.start_date,
                evidence.end_date,
                authority,
                evidence.document_sha256.lower(),
                _normalize_utc(evidence.observed_at),
            ),
        )
        if cursor.lastrowid:
            return int(cursor.lastrowid)
        row = fetch_one(
            conn,
            """SELECT id FROM corporate_action_coverage
               WHERE symbol = ? AND start_date = ? AND end_date = ?
                 AND source_authority = ? AND document_sha256 = ?""",
            (
                evidence.symbol.strip().upper(),
                evidence.start_date,
                evidence.end_date,
                authority,
                evidence.document_sha256.lower(),
            ),
        )
    return int(row["id"])


def import_benchmark_total_return(
    db_path: Path,
    evidence: BenchmarkTotalReturnEvidence,
) -> int:
    """Insert one immutable, independently evidenced benchmark TR value."""
    authority = evidence.source_authority.strip().upper()
    if authority not in _OFFICIAL_AUTHORITIES:
        raise ProvenanceError(
            "Benchmark total-return evidence requires an official source"
        )
    _validate_hash(evidence.document_sha256, "document_sha256")
    if float(evidence.index_value) <= 0:
        raise ProvenanceError("index_value must be positive")
    status = evidence.verification_status.strip().lower()
    if status not in {"verified", "conflict"}:
        raise ProvenanceError("Invalid benchmark verification_status")
    with connect_rw(db_path) as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO benchmark_total_return_history
               (symbol, price_date, index_value, source_authority,
                source_url, document_sha256, observed_at,
                verification_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                evidence.symbol.strip().upper(),
                evidence.price_date,
                float(evidence.index_value),
                authority,
                evidence.source_url,
                evidence.document_sha256.lower(),
                _normalize_utc(evidence.observed_at),
                status,
            ),
        )
        if cursor.lastrowid:
            return int(cursor.lastrowid)
        row = fetch_one(
            conn,
            """SELECT id FROM benchmark_total_return_history
               WHERE symbol = ? AND price_date = ?
                 AND source_authority = ? AND document_sha256 = ?""",
            (
                evidence.symbol.strip().upper(),
                evidence.price_date,
                authority,
                evidence.document_sha256.lower(),
            ),
        )
    return int(row["id"])


def import_manifest(db_path: Path, manifest_path: Path) -> dict[str, int]:
    """Import a reviewable JSON manifest of official evidence."""
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ProvenanceError("Manifest root must be an object")
    archived_documents = _archive_manifest_documents(
        db_path, manifest_path, payload
    )
    filings = shares = prices = actions = coverage = benchmarks = 0
    for item in payload.get("filings", []):
        import_filing_revision(db_path, FilingEvidence(**item))
        filings += 1
    for item in payload.get("shares_outstanding", []):
        import_shares_outstanding(db_path, **item)
        shares += 1
    for item in payload.get("prices", []):
        import_price_observation(db_path, PriceEvidence(**item))
        prices += 1
    for item in payload.get("corporate_actions", []):
        import_corporate_action(
            db_path, CorporateActionEvidence(**item)
        )
        actions += 1
    for item in payload.get("corporate_action_coverage", []):
        import_corporate_action_coverage(
            db_path, CorporateActionCoverageEvidence(**item)
        )
        coverage += 1
    for item in payload.get("benchmark_total_return", []):
        import_benchmark_total_return(
            db_path, BenchmarkTotalReturnEvidence(**item)
        )
        benchmarks += 1
    batch_id = _import_official_batch(
        db_path, manifest_path, payload
    )
    return {
        "filings": filings,
        "shares_outstanding": shares,
        "prices": prices,
        "corporate_actions": actions,
        "corporate_action_coverage": coverage,
        "benchmark_total_return": benchmarks,
        "documents": archived_documents,
        "official_batch_id": batch_id or 0,
    }


def _import_official_batch(
    db_path: Path,
    manifest_path: Path,
    payload: dict[str, Any],
) -> int | None:
    batch = payload.get("batch")
    classifications = payload.get("symbol_classifications", [])
    if batch is None and not classifications:
        return None
    if not isinstance(batch, dict):
        raise ProvenanceError(
            "A batch object is required with symbol_classifications"
        )
    if not isinstance(classifications, list) or not classifications:
        raise ProvenanceError(
            "symbol_classifications must be a non-empty array"
        )
    authority = str(batch.get("source_authority") or "").upper()
    if authority not in _OFFICIAL_AUTHORITIES:
        raise ProvenanceError("Batch requires an official source authority")
    as_of_year = int(batch.get("as_of_year") or 0)
    as_of_quarter = int(batch.get("as_of_quarter") or 0)
    if as_of_year < 2000 or as_of_quarter not in {1, 2, 3, 4}:
        raise ProvenanceError("Invalid batch as_of_year/as_of_quarter")
    cutoff = str(batch.get("classification_cutoff") or "")
    if "T" not in cutoff:
        raise ProvenanceError(
            "classification_cutoff must be a full timestamp"
        )
    observed_at = _normalize_utc(str(batch.get("observed_at") or ""))
    manifest_digest = file_sha256(manifest_path)
    allowed = {
        "verified",
        "not_published",
        "not_applicable",
        "source_empty",
        "ingestion_missing",
        "conflict",
    }
    normalized: list[tuple[Any, ...]] = []
    seen: set[str] = set()
    for item in classifications:
        if not isinstance(item, dict):
            raise ProvenanceError(
                "Each symbol classification must be an object"
            )
        symbol = str(item.get("symbol") or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{3,10}", symbol):
            raise ProvenanceError("Invalid classification symbol")
        if symbol in seen:
            raise ProvenanceError(
                f"Duplicate symbol classification: {symbol}"
            )
        seen.add(symbol)
        status = str(item.get("status") or "").strip().lower()
        if status not in allowed:
            raise ProvenanceError(
                f"Invalid classification status for {symbol}"
            )
        item_authority = str(
            item.get("source_authority") or authority
        ).upper()
        if item_authority not in _OFFICIAL_AUTHORITIES:
            raise ProvenanceError(
                f"Invalid source authority for {symbol}"
            )
        digest = str(item.get("document_sha256") or "").lower()
        _validate_hash(digest, "document_sha256")
        normalized.append(
            (
                symbol,
                status,
                item_authority,
                item.get("source_url"),
                digest,
                _normalize_utc(
                    str(item.get("observed_at") or observed_at)
                ),
                item.get("reason"),
            )
        )

    with connect_rw(db_path) as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO official_provenance_batches
               (manifest_sha256, as_of_year, as_of_quarter,
                classification_cutoff, source_authority, observed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                manifest_digest,
                as_of_year,
                as_of_quarter,
                _normalize_utc(cutoff),
                authority,
                observed_at,
            ),
        )
        if cursor.lastrowid:
            batch_id = int(cursor.lastrowid)
        else:
            row = fetch_one(
                conn,
                """SELECT id FROM official_provenance_batches
                   WHERE manifest_sha256 = ?""",
                (manifest_digest,),
            )
            batch_id = int(row["id"])
        conn.executemany(
            """INSERT OR IGNORE INTO official_symbol_classifications
               (batch_id, symbol, status, source_authority, source_url,
                document_sha256, observed_at, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(batch_id, *row) for row in normalized],
        )
    return batch_id


def activate_official_financial_version(
    db_path: Path,
    batch_id: int,
) -> dict[str, Any]:
    """Promote a complete official classification batch atomically."""
    from .financial_snapshot import (
        POINT_IN_TIME_METHODOLOGY,
        _required_investment_symbols,
    )

    with connect(db_path) as conn:
        batch = fetch_one(
            conn,
            """SELECT * FROM official_provenance_batches WHERE id = ?""",
            (int(batch_id),),
        )
        if not batch:
            raise ProvenanceError("Official provenance batch not found")
        rows = conn.execute(
            """SELECT * FROM official_symbol_classifications
               WHERE batch_id = ? ORDER BY symbol""",
            (int(batch_id),),
        ).fetchall()
        classifications = [dict(row) for row in rows]
    required = _required_investment_symbols(db_path)
    by_symbol = {row["symbol"]: row for row in classifications}
    missing = sorted(required - set(by_symbol))
    if missing:
        raise ProvenanceError(
            "Required symbols lack official classification: "
            + ", ".join(missing[:30])
        )
    blocked = [
        row
        for row in classifications
        if row["symbol"] in required
        and row["status"]
        not in {"verified", "not_published", "not_applicable"}
    ]
    if blocked:
        raise ProvenanceError(
            "Official classifications contain blockers: "
            + ", ".join(
                f"{row['symbol']}:{row['status']}" for row in blocked[:30]
            )
        )
    cutoff = str(batch["classification_cutoff"])
    verified_symbols = sorted(
        row["symbol"]
        for row in classifications
        if row["status"] == "verified"
    )
    missing_facts: list[str] = []
    with connect(db_path) as conn:
        for symbol in verified_symbols:
            fact = fetch_one(
                conn,
                """SELECT 1 AS ok
                   FROM financial_period_facts f
                   JOIN financial_filing_revisions r
                     ON r.id = f.filing_revision_id
                   WHERE f.symbol = ?
                     AND f.is_independent_quarter = 1
                     AND r.verification_status = 'verified'
                     AND r.available_at <= ?
                     AND NOT EXISTS (
                       SELECT 1
                       FROM financial_filing_revisions conflict
                       WHERE conflict.symbol = f.symbol
                         AND conflict.year = f.year
                         AND conflict.quarter = f.quarter
                         AND conflict.verification_status = 'conflict'
                         AND conflict.available_at <= ?
                         AND NOT EXISTS (
                           SELECT 1
                           FROM financial_filing_revisions resolution
                           WHERE resolution.supersedes_revision_id =
                                 conflict.id
                             AND resolution.verification_status = 'verified'
                             AND resolution.available_at <= ?
                         )
                     )
                   LIMIT 1""",
                (symbol, cutoff, cutoff, cutoff),
            )
            if not fact:
                missing_facts.append(symbol)
        revision_rows = fetch_all(
            conn,
            """SELECT r.id, r.symbol, r.year, r.quarter,
                      r.statement_scope, r.revision_number,
                      r.content_sha256, r.available_at
               FROM financial_filing_revisions r
               JOIN financial_period_facts f
                 ON f.filing_revision_id = r.id
               WHERE r.verification_status = 'verified'
                 AND f.is_independent_quarter = 1
                 AND r.available_at <= ?
               ORDER BY r.symbol, r.year, r.quarter,
                        r.revision_number, r.id""",
            (cutoff,),
        )
    if missing_facts:
        raise ProvenanceError(
            "Verified classifications lack verified EPS facts: "
            + ", ".join(missing_facts[:30])
        )
    digest_payload = {
        "batch_id": int(batch_id),
        "manifest_sha256": batch["manifest_sha256"],
        "classifications": classifications,
        "revisions": revision_rows,
    }
    content_hash = hashlib.sha256(
        json.dumps(
            digest_payload,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with connect_rw(db_path) as conn:
        existing = fetch_one(
            conn,
            """SELECT id FROM financial_data_versions
               WHERE content_hash = ?
                 AND official_provenance_ready = 1""",
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
                    quality_issues_json, provenance_batch_id)
                   VALUES ('OFFICIAL', 'reviewed_manifest_append_only_v1',
                           ?, ?, ?, ?, ?, 0, 1, 100, ?, ?, 1,
                           'official_verified', '[]', ?)""",
                (
                    int(batch["as_of_year"]),
                    int(batch["as_of_quarter"]),
                    content_hash,
                    len(revision_rows),
                    len(verified_symbols),
                    len(revision_rows),
                    POINT_IN_TIME_METHODOLOGY,
                    int(batch_id),
                ),
            )
            version_id = int(cursor.lastrowid)
        conn.execute(
            """UPDATE financial_data_versions
               SET is_active = 0 WHERE is_active = 1"""
        )
        conn.execute(
            """UPDATE financial_data_versions
               SET is_active = 1 WHERE id = ?""",
            (version_id,),
        )
    return {
        "financial_data_version_id": version_id,
        "provenance_batch_id": int(batch_id),
        "content_hash": content_hash,
        "verified_revision_count": len(revision_rows),
        "verified_symbol_count": len(verified_symbols),
        "required_symbol_count": len(required),
        "point_in_time_ready": True,
    }


def _archive_manifest_documents(
    db_path: Path,
    manifest_path: Path,
    payload: dict[str, Any],
) -> int:
    documents = payload.get("documents", [])
    if not isinstance(documents, list):
        raise ProvenanceError("documents must be an array")
    supplied: dict[str, Path] = {}
    for item in documents:
        if not isinstance(item, dict):
            raise ProvenanceError("Each document entry must be an object")
        expected = str(item.get("sha256") or "").lower()
        _validate_hash(expected, "documents[].sha256")
        relative = Path(str(item.get("path") or ""))
        source = (
            relative
            if relative.is_absolute()
            else manifest_path.parent / relative
        )
        if not source.is_file():
            raise ProvenanceError(f"Evidence document not found: {source}")
        if file_sha256(source) != expected:
            raise ProvenanceError(
                f"Evidence checksum mismatch: {source}"
            )
        supplied[expected] = source

    required_hashes = {
        str(item[field]).lower()
        for collection, field in (
            ("filings", "document_sha256"),
            ("shares_outstanding", "document_sha256"),
            ("prices", "payload_sha256"),
            ("corporate_actions", "document_sha256"),
            ("corporate_action_coverage", "document_sha256"),
            ("benchmark_total_return", "document_sha256"),
            ("symbol_classifications", "document_sha256"),
        )
        for item in payload.get(collection, [])
        if isinstance(item, dict) and item.get(field)
    }
    missing = sorted(required_hashes - set(supplied))
    if missing:
        raise ProvenanceError(
            "Manifest does not include local evidence for hashes: "
            + ", ".join(missing[:20])
        )

    repository = db_path.parent / "provenance_documents"
    objects = repository / "objects"
    manifests = repository / "manifests"
    objects.mkdir(parents=True, exist_ok=True)
    manifests.mkdir(parents=True, exist_ok=True)
    for digest, source in supplied.items():
        suffix = source.suffix.lower()
        target = objects / f"{digest}{suffix}"
        if target.exists() and file_sha256(target) != digest:
            raise ProvenanceError(
                f"Archived evidence hash conflict: {target}"
            )
        if not target.exists():
            shutil.copy2(source, target)
    manifest_digest = file_sha256(manifest_path)
    manifest_target = manifests / f"{manifest_digest}.json"
    if not manifest_target.exists():
        shutil.copy2(manifest_path, manifest_target)
    return len(supplied)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_available_at(
    db_path: Path,
    published_at: str | None,
    first_observed_at: str,
    basis: str,
) -> str:
    if basis == "official_timestamp":
        if not published_at or "T" not in published_at:
            raise ProvenanceError(
                "official_timestamp requires a full publication timestamp"
            )
        return _normalize_utc(published_at)
    if basis == "official_date_next_session":
        if not published_at:
            raise ProvenanceError(
                "official_date_next_session requires published_at"
            )
        public_date = published_at[:10]
        with connect(db_path) as conn:
            row = fetch_one(
                conn,
                """SELECT MIN(time) AS next_session
                   FROM stock_price_history
                   WHERE symbol = 'VNINDEX' AND time > ?""",
                (public_date,),
            )
        next_session = (row or {}).get("next_session")
        if not next_session:
            raise ProvenanceError(
                f"No market session exists after {public_date}"
            )
        return _normalize_utc(f"{next_session}T00:00:00+07:00")
    if basis == "live_observed":
        return _normalize_utc(first_observed_at)
    raise ProvenanceError("Invalid availability_basis")


def _validate_hash(value: str, field: str) -> None:
    if not _SHA256.fullmatch(value.lower()):
        raise ProvenanceError(f"{field} must be a 64-character SHA-256")


def _normalize_utc(value: str) -> str:
    text = str(value).strip()
    if not text:
        raise ProvenanceError("Timestamp must not be empty")
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProvenanceError(f"Invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_MARKET_TIMEZONE)
    return (
        parsed.astimezone(dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
