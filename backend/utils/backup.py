"""SQLite database backup utility.

Usage:
    python -m backend.utils.backup                   # backup to ./backups/
    python -m backend.utils.backup --dest /path/to   # custom destination
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from ..config import get_config

log = logging.getLogger(__name__)


def backup_database(
    db_path: Path | None = None,
    dest_dir: Path | None = None,
    max_backups: int = 10,
) -> Path:
    """Create a timestamped backup of the SQLite database.

    Uses SQLite's online backup API for a consistent snapshot even while
    the server is running (WAL mode safe).

    Keeps at most `max_backups` recent backups, deleting oldest first.
    Returns the path to the new backup file.
    """
    if db_path is None:
        db_path = get_config().db_path
    if dest_dir is None:
        dest_dir = db_path.parent / "backups"

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    dest_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{db_path.stem}_{timestamp}.db"
    backup_path = dest_dir / backup_name

    # Use SQLite backup API for WAL-safe copy
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(backup_path))
    try:
        src.backup(dst)
        check = dst.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            raise RuntimeError(
                f"Backup integrity check failed: {check[0] if check else 'no result'}"
            )
        log.info("Backup created: %s (%.1f MB)",
                 backup_path, backup_path.stat().st_size / 1e6)
    finally:
        dst.close()
        src.close()

    digest = _sha256_file(backup_path)
    backup_path.with_suffix(".db.sha256").write_text(
        f"{digest}  {backup_path.name}\n",
        encoding="ascii",
    )

    # Rotate old backups and their checksums.
    backups = sorted(dest_dir.glob(f"{db_path.stem}_*.db"), reverse=True)
    for old in backups[max_backups:]:
        checksum = old.with_suffix(".db.sha256")
        if checksum.exists():
            checksum.unlink()
        old.unlink()
        log.info("Deleted old backup: %s", old.name)

    return backup_path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_backup(path: Path) -> dict[str, object]:
    """Verify checksum and SQLite integrity without restoring over live data."""
    if not path.exists():
        raise FileNotFoundError(path)
    checksum_path = path.with_suffix(".db.sha256")
    expected = None
    if checksum_path.exists():
        expected = checksum_path.read_text(encoding="ascii").split()[0]
    actual = _sha256_file(path)
    if expected and actual != expected:
        raise RuntimeError("Backup checksum mismatch")
    with sqlite3.connect(str(path)) as conn:
        quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
    if quick_check != "ok":
        raise RuntimeError(f"Backup quick_check failed: {quick_check}")
    return {
        "path": str(path),
        "sha256": actual,
        "quick_check": quick_check,
        "size_bytes": path.stat().st_size,
    }


def ensure_daily_backup(
    db_path: Path,
    *,
    max_backups: int = 5,
) -> Path:
    """Create at most one verified backup per local calendar day."""
    dest_dir = db_path.parent / "backups"
    today_prefix = f"{db_path.stem}_{datetime.now():%Y%m%d}_"
    existing = sorted(dest_dir.glob(f"{today_prefix}*.db"), reverse=True)
    if existing:
        result = verify_backup(existing[0])
        checksum = existing[0].with_suffix(".db.sha256")
        if not checksum.exists():
            checksum.write_text(
                f"{result['sha256']}  {existing[0].name}\n",
                encoding="ascii",
            )
        backup_path = existing[0]
    else:
        backup_path = backup_database(db_path, dest_dir, max_backups)
    ensure_daily_evidence_backup(
        db_path.parent / "provenance_documents",
        dest_dir,
        max_backups=max_backups,
    )
    return backup_path


def ensure_daily_evidence_backup(
    evidence_dir: Path,
    dest_dir: Path,
    *,
    max_backups: int = 5,
) -> Path:
    """Copy the append-only evidence repository with a checksum manifest."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    today_prefix = f"evidence_{datetime.now():%Y%m%d}_"
    existing = sorted(
        (
            path
            for path in dest_dir.glob(f"{today_prefix}*")
            if path.is_dir()
        ),
        reverse=True,
    )
    if existing:
        verify_evidence_backup(existing[0])
        return existing[0]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = dest_dir / f"evidence_{timestamp}"
    files_dir = destination / "files"
    files_dir.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, object]] = []
    if evidence_dir.exists():
        for source in sorted(
            path for path in evidence_dir.rglob("*") if path.is_file()
        ):
            relative = source.relative_to(evidence_dir)
            target = files_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            entries.append(
                {
                    "path": relative.as_posix(),
                    "sha256": _sha256_file(target),
                    "size_bytes": target.stat().st_size,
                }
            )
    manifest = {
        "created_at": datetime.now().astimezone().isoformat(),
        "source": str(evidence_dir.resolve()),
        "files": entries,
    }
    (destination / "manifest.json").write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    verify_evidence_backup(destination)

    backups = sorted(
        (
            path
            for path in dest_dir.glob("evidence_*")
            if path.is_dir()
        ),
        reverse=True,
    )
    for old in backups[max_backups:]:
        shutil.rmtree(old)
        log.info("Deleted old evidence backup: %s", old.name)
    return destination


def verify_evidence_backup(path: Path) -> dict[str, object]:
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"Evidence manifest missing: {path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in payload.get("files", []):
        target = path / "files" / str(entry["path"])
        if not target.exists():
            raise RuntimeError(f"Evidence file missing: {entry['path']}")
        if _sha256_file(target) != entry["sha256"]:
            raise RuntimeError(
                f"Evidence checksum mismatch: {entry['path']}"
            )
    return {
        "path": str(path),
        "file_count": len(payload.get("files", [])),
        "manifest": str(manifest_path),
    }


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Backup SQLite database")
    parser.add_argument("--dest", type=Path, help="Backup destination directory")
    parser.add_argument("--max", type=int, default=10, help="Max backups to keep")
    args = parser.parse_args()

    cfg = get_config()
    path = backup_database(cfg.db_path, args.dest, args.max)
    print(f"Backup saved: {path}")


if __name__ == "__main__":
    main()
