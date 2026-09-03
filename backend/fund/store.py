"""SQLite persistence for the simple fund-planning workflow."""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..database.connection import connect, connect_rw, fetch_all, fetch_one

ALLOWED_STRATEGIES = {"TTM_20Q", "LAST_8Q_PLUS"}
ALLOWED_SELECT_PCTS = {10.0, 12.0, 14.0, 16.0}


def get_preferences(db_path: Path) -> dict[str, Any]:
    with connect(db_path) as conn:
        row = fetch_one(
            conn,
            """SELECT strategy, select_pct, updated_at
               FROM fund_preferences WHERE id = 1""",
        )
    return row or {
        "strategy": "LAST_8Q_PLUS",
        "select_pct": 10.0,
        "updated_at": None,
    }


def save_preferences(
    db_path: Path, strategy: str, select_pct: float
) -> dict[str, Any]:
    strategy = strategy.strip().upper()
    pct = float(select_pct)
    if strategy not in ALLOWED_STRATEGIES:
        raise ValueError(f"Chiến lược không hợp lệ: {strategy}")
    if pct not in ALLOWED_SELECT_PCTS:
        raise ValueError("Tỷ lệ chọn phải là 10%, 12%, 14% hoặc 16%")

    with connect_rw(db_path) as conn:
        conn.execute(
            """INSERT INTO fund_preferences
               (id, strategy, select_pct, updated_at)
               VALUES (1, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET
                 strategy = excluded.strategy,
                 select_pct = excluded.select_pct,
                 updated_at = CURRENT_TIMESTAMP""",
            (strategy, pct),
        )
    return get_preferences(db_path)


def normalize_holdings(
    db_path: Path, holdings: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Validate, uppercase, and combine duplicate holding rows."""
    combined: dict[str, int] = {}
    for item in holdings:
        symbol = str(item.get("symbol", "")).strip().upper()
        raw_shares = item.get("shares")
        if not symbol:
            raise ValueError("Mỗi dòng danh mục phải có mã cổ phiếu")
        if isinstance(raw_shares, bool) or not isinstance(raw_shares, int):
            raise ValueError(f"Số lượng của {symbol} phải là số nguyên")
        if raw_shares < 0:
            raise ValueError(f"Số lượng của {symbol} không được âm")
        combined[symbol] = combined.get(symbol, 0) + raw_shares

    symbols = sorted(sym for sym, shares in combined.items() if shares > 0)
    if symbols:
        placeholders = ",".join("?" for _ in symbols)
        with connect(db_path) as conn:
            rows = fetch_all(
                conn,
                f"SELECT ticker FROM stocks WHERE ticker IN ({placeholders})",
                tuple(symbols),
            )
        known = {row["ticker"] for row in rows}
        unknown = [symbol for symbol in symbols if symbol not in known]
        if unknown:
            raise ValueError(f"Mã không tồn tại: {', '.join(unknown)}")

    return [{"symbol": symbol, "shares": combined[symbol]} for symbol in symbols]


def get_holdings(db_path: Path) -> dict[str, Any]:
    with connect(db_path) as conn:
        rows = fetch_all(
            conn,
            """SELECT symbol, shares, updated_at
               FROM fund_holdings ORDER BY symbol""",
        )
    updated_at = max((row["updated_at"] for row in rows), default=None)
    return {
        "holdings": [
            {"symbol": row["symbol"], "shares": int(row["shares"])}
            for row in rows
        ],
        "updated_at": updated_at,
    }


def replace_holdings(
    db_path: Path, holdings: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    normalized = normalize_holdings(db_path, holdings)
    with connect_rw(db_path) as conn:
        conn.execute("DELETE FROM fund_holdings")
        conn.executemany(
            """INSERT INTO fund_holdings (symbol, shares, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)""",
            [(item["symbol"], item["shares"]) for item in normalized],
        )
    return get_holdings(db_path)


def delete_holdings(db_path: Path) -> bool:
    with connect_rw(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM fund_holdings").fetchone()[0]
        conn.execute("DELETE FROM fund_holdings")
    return bool(count)
