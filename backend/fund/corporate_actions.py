"""Verified corporate-action ledger used by the investment planner."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..database.connection import connect, fetch_all, fetch_one


class CorporateActionError(RuntimeError):
    """Raised when a holding period cannot be reproduced safely."""


@dataclass(frozen=True)
class LedgerResult:
    symbol: str
    share_factor: float
    cash_vnd_per_initial_share: float
    event_count: int


def build_verified_ledger(
    db_path: Path,
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> dict[str, LedgerResult]:
    """Apply verified actions after purchase through the valuation date.

    A buyer at the opening price on an ex-date is not entitled to that
    ex-date's distribution, therefore the interval is ``(start, end]``.
    Cash dividends remain cash. Stock dividends and splits multiply shares.
    Rights issues and unknown actions fail closed.
    """
    normalized = sorted({symbol.strip().upper() for symbol in symbols})
    if not normalized:
        return {}
    if end_date < start_date:
        raise CorporateActionError("Ngày định giá đứng trước ngày thực thi")

    with connect(db_path) as conn:
        _require_tables(conn)
        for symbol in normalized:
            coverage = fetch_one(
                conn,
                """SELECT 1 AS ok
                   FROM corporate_action_coverage
                   WHERE symbol = ?
                     AND coverage_status = 'verified'
                     AND start_date <= ?
                     AND end_date >= ?
                   LIMIT 1""",
                (symbol, start_date, end_date),
            )
            if not coverage:
                raise CorporateActionError(
                    f"Thiếu coverage corporate action đã xác minh cho "
                    f"{symbol} ({start_date}–{end_date})"
                )

        placeholders = ",".join("?" for _ in normalized)
        blocked = fetch_all(
            conn,
            f"""SELECT symbol, action_type, ex_date, verification_status
                FROM corporate_actions
                WHERE symbol IN ({placeholders})
                  AND ex_date > ? AND ex_date <= ?
                  AND (
                    verification_status IN ('conflict', 'unsupported')
                    OR action_type IN ('rights_issue', 'other')
                  )
                ORDER BY symbol, ex_date""",
            (*normalized, start_date, end_date),
        )
        if blocked:
            details = ", ".join(
                f"{row['symbol']}:{row['action_type']}@{row['ex_date']}"
                for row in blocked[:20]
            )
            raise CorporateActionError(
                "Có corporate action xung đột/chưa hỗ trợ: " + details
            )
        actions = fetch_all(
            conn,
            f"""SELECT symbol, action_type, ex_date, payment_date,
                       cash_vnd_per_share, share_factor
                FROM corporate_actions
                WHERE symbol IN ({placeholders})
                  AND ex_date > ? AND ex_date <= ?
                  AND verification_status = 'verified'
                ORDER BY symbol, ex_date, id""",
            (*normalized, start_date, end_date),
        )

    by_symbol_date: dict[
        str, dict[str, list[dict]]
    ] = defaultdict(lambda: defaultdict(list))
    for row in actions:
        by_symbol_date[str(row["symbol"])][str(row["ex_date"])].append(row)

    results: dict[str, LedgerResult] = {}
    for symbol in normalized:
        share_factor = 1.0
        cash_per_initial_share = 0.0
        event_count = 0
        for ex_date in sorted(by_symbol_date[symbol]):
            rows = by_symbol_date[symbol][ex_date]
            # Same-day entitlements are all based on the shares held before
            # that ex-date. Apply cash first, then aggregate share factors.
            before_factor = share_factor
            same_day_factor = 1.0
            for row in rows:
                action_type = str(row["action_type"])
                if action_type == "cash_dividend":
                    cash = row.get("cash_vnd_per_share")
                    if cash is None or float(cash) < 0:
                        raise CorporateActionError(
                            f"Cổ tức tiền mặt {symbol}@{ex_date} thiếu giá trị"
                        )
                    payment_date = row.get("payment_date")
                    if not payment_date:
                        raise CorporateActionError(
                            f"Cổ tức tiền mặt {symbol}@{ex_date} "
                            "thiếu ngày thanh toán"
                        )
                    if str(payment_date) <= end_date:
                        cash_per_initial_share += before_factor * float(cash)
                elif action_type in {"stock_dividend", "split"}:
                    factor = row.get("share_factor")
                    if factor is None or float(factor) <= 0:
                        raise CorporateActionError(
                            f"Sự kiện cổ phiếu {symbol}@{ex_date} "
                            "thiếu hệ số hợp lệ"
                        )
                    same_day_factor *= float(factor)
                else:
                    raise CorporateActionError(
                        f"Chưa hỗ trợ {action_type} cho {symbol}@{ex_date}"
                    )
                event_count += 1
            share_factor *= same_day_factor
        results[symbol] = LedgerResult(
            symbol=symbol,
            share_factor=share_factor,
            cash_vnd_per_initial_share=cash_per_initial_share,
            event_count=event_count,
        )
    return results


def _require_tables(conn: sqlite3.Connection) -> None:
    present = {
        str(row["name"])
        for row in fetch_all(
            conn,
            """SELECT name FROM sqlite_master
               WHERE type = 'table'
                 AND name IN (
                    'corporate_actions', 'corporate_action_coverage'
                 )""",
        )
    }
    required = {"corporate_actions", "corporate_action_coverage"}
    if present != required:
        raise CorporateActionError(
            "Kho corporate action chưa được khởi tạo đầy đủ"
        )
