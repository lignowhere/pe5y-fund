"""SQLite connection helpers adapted from old project webapp/db.py."""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import quote

log = logging.getLogger(__name__)

# Serialized write lock — ensures only one thread writes at a time.
# WAL mode allows concurrent reads, but writes still need serialization
# to avoid "database is locked" under ThreadPoolExecutor load.
_write_lock = threading.Lock()


def _configure(conn: sqlite3.Connection, *, readonly: bool = False) -> None:
    """Apply common PRAGMAs to a fresh connection."""
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")


@contextmanager
def connect(db_path: Path, *, readonly: bool = True) -> Iterator[sqlite3.Connection]:
    """Open SQLite with optional read-only mode."""
    conn: Optional[sqlite3.Connection] = None
    try:
        if readonly:
            try:
                uri = f"file:{quote(str(db_path))}?mode=ro"
                conn = sqlite3.connect(uri, uri=True)
            except sqlite3.OperationalError:
                log.warning("Read-only URI mode unsupported, falling back to regular connection")
                conn = sqlite3.connect(str(db_path))
        else:
            conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        _configure(conn, readonly=readonly)
        yield conn
    finally:
        if conn is not None:
            conn.close()


@contextmanager
def connect_rw(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open SQLite in read-write mode with auto-commit.

    Uses a process-wide write lock to serialize concurrent writers
    (e.g. ThreadPoolExecutor workers inserting price data).
    """
    conn: Optional[sqlite3.Connection] = None
    with _write_lock:
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            _configure(conn)
            yield conn
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if conn is not None:
                conn.close()


def fetch_all(
    conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    cur = conn.execute(query, params)
    return [dict(r) for r in cur.fetchall()]


def fetch_one(
    conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()
) -> Optional[dict[str, Any]]:
    cur = conn.execute(query, params)
    row = cur.fetchone()
    return dict(row) if row is not None else None


def search_symbols(
    conn: sqlite3.Connection, q: str, limit: int = 20
) -> list[dict[str, Any]]:
    q = (q or "").strip().upper()
    return fetch_all(
        conn,
        """
        SELECT s.ticker, s.organ_name,
               GROUP_CONCAT(DISTINCT se.exchange) AS exchanges
        FROM stocks s
        LEFT JOIN stock_exchange se ON se.ticker = s.ticker
        WHERE LENGTH(s.ticker) = 3
          AND s.ticker GLOB '[A-Z][A-Z][A-Z]'
          AND (s.ticker LIKE ? OR UPPER(COALESCE(s.organ_name,'')) LIKE ?)
        GROUP BY s.ticker
        ORDER BY s.ticker
        LIMIT ?
        """,
        (f"{q}%", f"%{q}%", limit),
    )


def get_active_symbols(conn: sqlite3.Connection) -> list[str]:
    """All 3-letter stock symbols on major exchanges."""
    rows = fetch_all(
        conn,
        """
        SELECT DISTINCT s.ticker
        FROM stocks s
        JOIN stock_exchange se ON se.ticker = s.ticker
        WHERE LENGTH(s.ticker) = 3
          AND s.ticker GLOB '[A-Z][A-Z][A-Z]'
          AND se.exchange IN ('HSX', 'HNX', 'UPCOM')
        ORDER BY s.ticker
        """,
    )
    return [r["ticker"] for r in rows]
