"""Explicit owner attestation for using the existing local vendor database."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from ..data.financial_snapshot import get_active_financial_version
from ..database.connection import connect, connect_rw, fetch_one
from ..utils.backup import verify_backup

TRUSTED_LOCAL_METHODOLOGY = "user_confirmed_local_v1"
TRUSTED_LOCAL_PIT_POLICY = "owner_confirmed_existing_database"
TRUSTED_LOCAL = "trusted_local"
DEFAULT_ATTESTATION_STATEMENT = (
    "The fund owner instructed the system to use the existing "
    "vietnam_stocks.db as accepted input without repeating official-document "
    "verification. This does not claim official exchange provenance."
)


class TrustedLocalError(RuntimeError):
    """Raised when a trusted-local attestation is missing or inconsistent."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_trusted_local_attestation(
    db_path: Path,
    backup_path: Path,
    *,
    source_database_sha256: str,
    statement: str = DEFAULT_ATTESTATION_STATEMENT,
    attested_by: str = "fund_owner",
) -> dict[str, Any]:
    """Record the owner's explicit acceptance after a verified backup."""
    backup = verify_backup(backup_path)
    financial_version = get_active_financial_version(db_path)
    if not financial_version:
        raise TrustedLocalError(
            "No active financial-data version is available to attest"
        )

    attested_at = dt.datetime.now(dt.timezone.utc).isoformat(
        timespec="seconds"
    )
    payload = {
        "financial_data_version_id": int(financial_version["id"]),
        "financial_content_hash": str(financial_version["content_hash"]),
        "source_database_sha256": source_database_sha256,
        "source_backup_path": str(backup_path.resolve()),
        "source_backup_sha256": str(backup["sha256"]),
        "statement": statement,
        "attested_by": attested_by,
        "attested_at": attested_at,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    attestation_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    with connect_rw(db_path) as conn:
        conn.execute(
            """UPDATE trusted_local_attestations
               SET is_active = 0, revoked_at = ?
               WHERE is_active = 1""",
            (attested_at,),
        )
        cur = conn.execute(
            """INSERT INTO trusted_local_attestations
               (financial_data_version_id, financial_content_hash,
                source_database_sha256, source_backup_path,
                source_backup_sha256, statement, attested_by, attested_at,
                is_active, attestation_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                payload["financial_data_version_id"],
                payload["financial_content_hash"],
                payload["source_database_sha256"],
                payload["source_backup_path"],
                payload["source_backup_sha256"],
                payload["statement"],
                payload["attested_by"],
                payload["attested_at"],
                attestation_hash,
            ),
        )
        attestation_id = int(cur.lastrowid)

    result = {
        "id": attestation_id,
        **payload,
        "attestation_hash": attestation_hash,
        "is_active": True,
    }
    manifest_path = db_path.parent / "trusted_local_attestation.json"
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return result


def get_active_trusted_local_attestation(
    db_path: Path,
) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        return fetch_one(
            conn,
            """SELECT *
               FROM trusted_local_attestations
               WHERE is_active = 1 AND revoked_at IS NULL
               ORDER BY id DESC LIMIT 1""",
        )

