"""Shared full-data synchronization used by the API and Windows task."""
from __future__ import annotations

import datetime as dt
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..config import (
    AppConfig,
    fail_strategy_config,
    get_config,
    get_pending_strategy_config,
    get_strategy_config_state,
    reload_config,
)
from ..database.connection import connect, connect_rw, fetch_all, fetch_one
from ..strategy.position_sizer import query_latest_price_date
from ..strategy.variants import STRATEGY_PARAMS
from ..fund.snapshots import (
    StrategySnapshotError,
    build_and_activate_snapshot_set,
    get_active_snapshot_status,
    strategy_config_fingerprint,
)
from ..fund.adjusted_prices import ensure_adjusted_performance_prices
from ..fund.cycle import PlannerDataError, resolve_active_cycle
from ..fund.store import get_preferences
from .financial_snapshot import (
    activate_staged_financials,
    financial_universe,
    get_active_financial_version,
    stage_vci_financials,
)
from .kbs_client import KBSClient
from .updater import (
    detect_missing_prices,
    probe_price_fallback,
    update_prices_stream,
)
from .vci_client import VCIClient
from ..utils.backup import ensure_daily_backup
from .updater import refresh_data_health_summary

log = logging.getLogger(__name__)


class SyncBusyError(RuntimeError):
    """Raised when another process already owns the data-sync lock."""


class PriceRefreshError(RuntimeError):
    """Raised when current portfolio prices cannot be refreshed safely."""


class SyncCancelled(RuntimeError):
    """Raised at a safe checkpoint after the user requests cancellation."""


@contextmanager
def _process_lock(db_path: Path) -> Iterator[bool]:
    """Cross-process non-blocking lock that is released automatically on exit."""
    lock_path = db_path.parent / ".pe5y-data-sync.lock"
    lock_path.touch(exist_ok=True)
    if lock_path.stat().st_size == 0:
        lock_path.write_bytes(b"0")
    handle = lock_path.open("r+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                pass
        yield acquired
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _start_run(db_path: Path) -> int:
    with connect_rw(db_path) as conn:
        conn.execute(
            """UPDATE data_sync_runs
               SET status = 'interrupted', stage = 'done',
                   finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP),
                   message = COALESCE(
                       message, 'Superseded after the prior process stopped'
                   )
               WHERE status = 'running'"""
        )
        cur = conn.execute(
            "INSERT INTO data_sync_runs (status, stage) VALUES ('running', 'prices')"
        )
        return int(cur.lastrowid)


def _update_run(db_path: Path, run_id: int, **values: object) -> None:
    allowed = {
        "status", "stage", "finished_at", "prices_updated", "prices_failed",
        "financials_updated", "financials_failed", "message",
        "financial_symbols_total", "financial_rows_staged",
        "financial_version_id", "snapshot_set_id",
        "price_symbols_total", "prices_processed",
    }
    clean = {key: value for key, value in values.items() if key in allowed}
    if not clean:
        return
    assignments = ", ".join(f"{key} = ?" for key in clean)
    with connect_rw(db_path) as conn:
        conn.execute(
            f"UPDATE data_sync_runs SET {assignments} WHERE id = ?",
            (*clean.values(), run_id),
        )


def request_sync_cancel(db_path: Path) -> bool:
    """Request cooperative cancellation at the next safe sync checkpoint."""
    with connect_rw(db_path) as conn:
        cur = conn.execute(
            """UPDATE data_sync_runs
               SET cancel_requested = 1
               WHERE id = (
                   SELECT id FROM data_sync_runs
                   WHERE status = 'running'
                   ORDER BY id DESC LIMIT 1
               )"""
        )
    return cur.rowcount > 0


def _raise_if_cancelled(db_path: Path, run_id: int) -> None:
    with connect(db_path) as conn:
        row = fetch_one(
            conn,
            """SELECT cancel_requested
               FROM data_sync_runs WHERE id = ?""",
            (run_id,),
        )
    if row and bool(row.get("cancel_requested")):
        raise SyncCancelled("Data synchronization cancelled by user")


def _completed_quarter(today: dt.date) -> tuple[int, int]:
    quarter = (today.month - 1) // 3
    if quarter:
        return today.year, quarter
    return today.year - 1, 4


def _adaptive_count_back(db_path: Path, symbols: list[str]) -> int:
    if not symbols:
        return 30
    placeholders = ",".join("?" for _ in symbols)
    with connect(db_path) as conn:
        row = fetch_one(
            conn,
            f"""SELECT MIN(latest) AS oldest FROM (
                  SELECT symbol, MAX(time) AS latest
                  FROM stock_price_history
                  WHERE symbol IN ({placeholders})
                  GROUP BY symbol
                )""",
            tuple(symbols),
        )
    try:
        oldest = dt.date.fromisoformat((row or {})["oldest"])
        calendar_days = max(1, (dt.date.today() - oldest).days)
        return min(3_500, max(30, calendar_days * 5 // 7 + 30))
    except (KeyError, TypeError, ValueError):
        return 260


def refresh_portfolio_prices(
    config: AppConfig, symbols: list[str]
) -> list[str]:
    """Synchronously refresh only symbols needed for one portfolio plan."""
    symbols = sorted({symbol.upper() for symbol in symbols if symbol})
    if not symbols:
        return []
    with _process_lock(config.db_path) as acquired:
        if not acquired:
            raise SyncBusyError("Một tiến trình đồng bộ dữ liệu khác đang chạy")
        # The page already checks broad-market freshness before reaching this
        # endpoint. Avoid another vendor round-trip when every required symbol
        # is already aligned to that fresh market session.
        reference_date = query_latest_price_date(config.db_path)
        recent_cutoff = (
            dt.date.today() - dt.timedelta(days=4)
        ).isoformat()
        if reference_date and reference_date >= recent_cutoff:
            with connect(config.db_path) as conn:
                latest_rows = fetch_all(
                    conn,
                    f"""SELECT h.symbol, MAX(h.time) AS latest
                        FROM stock_price_history h
                        JOIN market_price_metadata m
                          ON m.symbol = h.symbol AND m.price_date = h.time
                        WHERE h.symbol IN (
                          {','.join('?' for _ in symbols)}
                        )
                          AND m.is_provisional = 0
                          AND m.source = 'VCI'
                          AND m.raw_unit = 'THOUSAND_VND'
                          AND LENGTH(m.source_payload_sha256) = 64
                        GROUP BY h.symbol""",
                    tuple(symbols),
                )
            latest_map = {
                row["symbol"]: row["latest"] for row in latest_rows
            }
            if all(
                latest_map.get(symbol)
                and latest_map[symbol] >= reference_date
                for symbol in symbols
            ):
                return []
        try:
            with VCIClient(config.vci.rate_limit_rpm) as vci, \
                    KBSClient(config.kbs.rate_limit_rpm) as kbs:
                # VNINDEX establishes the latest market session even after the
                # computer has been offline for a long time.
                list(update_prices_stream(
                    config.db_path, ["VNINDEX"], vci, kbs, count_back=15,
                    max_workers=1,
                ))
                with connect(config.db_path) as conn:
                    ref = fetch_one(
                        conn,
                        """SELECT MAX(time) AS latest FROM stock_price_history
                           WHERE symbol = 'VNINDEX'""",
                    )
                    latest_rows = fetch_all(
                        conn,
                        f"""SELECT symbol, MAX(time) AS latest
                            FROM stock_price_history
                            WHERE symbol IN ({','.join('?' for _ in symbols)})
                            GROUP BY symbol""",
                        tuple(symbols),
                    )
                reference_date = (ref or {}).get("latest")
                latest_map = {
                    row["symbol"]: row["latest"] for row in latest_rows
                }
                stale = [
                    symbol for symbol in symbols
                    if not latest_map.get(symbol)
                    or (reference_date and latest_map[symbol] < reference_date)
                ]
                if stale:
                    count_back = _adaptive_count_back(config.db_path, stale)
                    list(update_prices_stream(
                        config.db_path, stale, vci, kbs,
                        count_back=count_back,
                    ))
        except Exception as exc:
            log.warning("Targeted portfolio price refresh failed: %s", exc)
            raise PriceRefreshError(
                "Không thể làm mới đầy đủ giá hiện tại. "
                "Hệ thống đã dừng tính để tránh trộn dữ liệu cũ và mới."
            ) from exc
    return []


def run_full_sync(config: AppConfig) -> dict[str, object]:
    """Synchronize stale prices first, then latest financial statements."""
    db_path = config.db_path
    with _process_lock(db_path) as acquired:
        if not acquired:
            return {"status": "already_running"}
        ensure_daily_backup(db_path, max_backups=5)
        run_id = _start_run(db_path)
        price_updated = price_failed = financial_updated = financial_failed = 0
        pending_config = get_pending_strategy_config()
        pending_config_id = pending_config[0] if pending_config else None
        snapshot_warning: str | None = None
        try:
            with VCIClient(config.vci.rate_limit_rpm) as vci, \
                    KBSClient(config.kbs.rate_limit_rpm) as kbs:
                # Establish the newest completed market session first.  Stale
                # detection must never use yesterday as its reference simply
                # because the local database had not seen today's close yet.
                list(
                    update_prices_stream(
                        db_path,
                        ["VNINDEX"],
                        vci,
                        kbs,
                        count_back=15,
                        max_workers=1,
                    )
                )
                probe_price_fallback(db_path, kbs)
                price_symbols = detect_missing_prices(
                    db_path,
                    min_trading_day_gap=1,
                    min_symbols_for_market_day=1,
                )
                price_symbols = [
                    symbol for symbol in price_symbols
                    if symbol != "VNINDEX"
                ]
                _update_run(
                    db_path,
                    run_id,
                    price_symbols_total=len(price_symbols) + 1,
                    prices_processed=1,
                )
                count_back = _adaptive_count_back(db_path, price_symbols)
                for progress in update_prices_stream(
                    db_path, price_symbols, vci, kbs, count_back=count_back
                ):
                    _raise_if_cancelled(db_path, run_id)
                    price_updated = progress.updated_so_far
                    price_failed = progress.failed_so_far
                    if (progress.index + 1) % 5 == 0 or (
                        progress.index + 1 == progress.total
                    ):
                        _update_run(
                            db_path,
                            run_id,
                            prices_updated=price_updated,
                            prices_failed=price_failed,
                            prices_processed=progress.index + 2,
                        )

                financial_version = get_active_financial_version(db_path)
                if _financial_refresh_due(financial_version):
                    financial_symbols = financial_universe(db_path)
                    _update_run(
                        db_path, run_id, stage="financials",
                        prices_updated=price_updated,
                        prices_failed=price_failed,
                        financial_symbols_total=len(financial_symbols),
                    )

                    def on_financial_progress(progress) -> None:
                        nonlocal financial_updated, financial_failed
                        _raise_if_cancelled(db_path, run_id)
                        financial_updated = progress.index + 1 - progress.failed_so_far
                        financial_failed = progress.failed_so_far
                        if (progress.index + 1) % 5 == 0 or (
                            progress.index + 1 == progress.total
                        ):
                            _update_run(
                                db_path,
                                run_id,
                                financials_updated=financial_updated,
                                financials_failed=financial_failed,
                                financial_rows_staged=progress.staged_so_far,
                            )

                    stage_stats = stage_vci_financials(
                        db_path,
                        run_id,
                        financial_symbols,
                        vci,
                        on_progress=on_financial_progress,
                    )
                    target_year, target_quarter = _completed_quarter(
                        dt.date.today()
                    )
                    activated = activate_staged_financials(
                        db_path,
                        run_id,
                        as_of_year=target_year,
                        as_of_quarter=target_quarter,
                        expected_symbols=len(financial_symbols),
                    )
                    financial_version = {
                        "id": activated.version_id,
                        "content_hash": activated.content_hash,
                    }
                    financial_updated = (
                        stage_stats["total"] - stage_stats["failed"]
                    )
                    financial_failed = stage_stats["failed"]
                else:
                    _update_run(
                        db_path,
                        run_id,
                        stage="financials_current",
                        financial_version_id=financial_version["id"],
                    )

                _raise_if_cancelled(db_path, run_id)
                snapshot_config = (
                    pending_config[1] if pending_config else config
                )
                financial_provenance_ready = bool(
                    financial_version.get("point_in_time_ready")
                    and financial_version.get("official_provenance_ready")
                )
                if not financial_provenance_ready:
                    snapshot_warning = (
                        "SNAPSHOT_NOT_VERIFIED: official filing provenance "
                        "is incomplete; automatic vendor sync cannot promote "
                        "an investment snapshot"
                    )
                    snapshot = get_active_snapshot_status(db_path)
                    snapshot_set_id = (
                        snapshot["snapshot_set_id"] if snapshot else None
                    )
                    _update_run(
                        db_path,
                        run_id,
                        stage="snapshot_blocked",
                        snapshot_set_id=snapshot_set_id,
                        message=snapshot_warning,
                    )
                elif pending_config or _snapshot_refresh_due(
                    snapshot_config, financial_version
                ):
                    _update_run(db_path, run_id, stage="backtest_snapshots")
                    try:
                        snapshot_result = build_and_activate_snapshot_set(
                            snapshot_config,
                            adjusted_price_client=vci,
                            require_adjusted_prices=True,
                            pending_config_version_id=pending_config_id,
                        )
                        snapshot_set_id = snapshot_result["snapshot_set_id"]
                        if pending_config_id is not None:
                            config = reload_config()
                        _update_run(
                            db_path,
                            run_id,
                            snapshot_set_id=snapshot_set_id,
                        )
                    except StrategySnapshotError as exc:
                        snapshot_warning = str(exc)
                        snapshot = get_active_snapshot_status(db_path)
                        snapshot_set_id = (
                            snapshot["snapshot_set_id"]
                            if snapshot else None
                        )
                        if pending_config_id is not None:
                            fail_strategy_config(
                                pending_config_id, snapshot_warning
                            )
                            pending_config_id = None
                        _update_run(
                            db_path,
                            run_id,
                            stage="snapshot_blocked",
                            snapshot_set_id=snapshot_set_id,
                            message=snapshot_warning[:500],
                        )
                else:
                    snapshot = get_active_snapshot_status(db_path)
                    snapshot_set_id = (
                        snapshot["snapshot_set_id"] if snapshot else None
                    )

                snapshot_status = get_active_snapshot_status(db_path)
                valuation_date = query_latest_price_date(db_path)
                if (
                    valuation_date
                    and snapshot_status
                    and (
                        snapshot_status.get("investment_ready")
                        or snapshot_status.get("user_confirmed_ready")
                    )
                ):
                    preferences = get_preferences(db_path)
                    try:
                        active_cycle = resolve_active_cycle(
                            config,
                            preferences["strategy"],
                            float(preferences["select_pct"]),
                        )
                        _update_run(db_path, run_id, stage="adjusted_prices")
                        ensure_adjusted_performance_prices(
                            config,
                            active_cycle,
                            valuation_date,
                            client=vci,
                            extra_symbols=[config.strategy.benchmark_symbol],
                        )
                    except PlannerDataError as exc:
                        # A new hold-year can start before its strategy snapshot exists.
                        # Raw price/financial data is still usable, so do not turn the
                        # whole data sync into a failed run for this expected condition.
                        snapshot_warning = (
                            "Đồng bộ dữ liệu đã hoàn tất, nhưng chưa làm mới được "
                            "giá hiệu suất điều chỉnh: "
                            f"{exc}"
                        )
                        _update_run(
                            db_path,
                            run_id,
                            stage="adjusted_prices_blocked",
                            message=snapshot_warning[:500],
                        )

            _cleanup_stale_sync_state(db_path, run_id)
            refresh_data_health_summary(db_path)
            _update_run(
                db_path, run_id, status="completed", stage="done",
                finished_at=dt.datetime.now(
                    dt.timezone.utc
                ).isoformat(timespec="seconds"),
                prices_updated=price_updated, prices_failed=price_failed,
                financials_updated=financial_updated,
                financials_failed=financial_failed,
                financial_version_id=financial_version["id"],
                snapshot_set_id=snapshot_set_id,
                message=snapshot_warning,
            )
            return {
                "status": "completed",
                "run_id": run_id,
                "financial_version_id": financial_version["id"],
                "snapshot_set_id": snapshot_set_id,
                "prices_failed": price_failed,
                "financials_failed": financial_failed,
                "warning": snapshot_warning,
            }
        except SyncCancelled as exc:
            _cleanup_stale_sync_state(db_path, run_id)
            _update_run(
                db_path,
                run_id,
                status="cancelled",
                stage="done",
                finished_at=dt.datetime.now(
                    dt.timezone.utc
                ).isoformat(timespec="seconds"),
                message=str(exc),
            )
            return {
                "status": "cancelled",
                "run_id": run_id,
                "message": str(exc),
            }
        except Exception as exc:
            log.exception("Full data sync failed")
            if pending_config_id is not None:
                fail_strategy_config(pending_config_id, str(exc))
            _cleanup_stale_sync_state(db_path, run_id)
            _update_run(
                db_path, run_id, status="failed", stage="done",
                finished_at=dt.datetime.now(
                    dt.timezone.utc
                ).isoformat(timespec="seconds"),
                message=str(exc)[:500],
            )
            return {"status": "failed", "run_id": run_id, "message": str(exc)}


def get_sync_status(db_path: Path) -> dict[str, object]:
    with connect(db_path) as conn:
        latest = fetch_one(
            conn, "SELECT * FROM data_sync_runs ORDER BY id DESC LIMIT 1"
        )
        partial = fetch_one(
            conn,
            """SELECT MAX(h.time) AS latest FROM (
                 SELECT h.time
                 FROM stock_price_history h
                 LEFT JOIN market_price_metadata m
                   ON m.symbol = h.symbol AND m.price_date = h.time
                 WHERE h.time >= date('now', '-45 days')
                   AND COALESCE(m.is_provisional, 0) = 0
                 GROUP BY h.time
                 HAVING COUNT(DISTINCT h.symbol) >= 5
               ) h""",
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
    with _process_lock(db_path) as lock_available:
        running = not lock_available
    if latest and latest.get("status") == "running" and not running:
        latest = {
            **latest,
            "status": "interrupted",
            "stage": "done",
            "message": "Tiến trình trước đã dừng ngoài dự kiến",
        }

    broad_date = query_latest_price_date(db_path)
    partial_date = (partial or {}).get("latest")
    cutoff = (dt.date.today() - dt.timedelta(days=4)).isoformat()
    prices_need_sync = not broad_date or broad_date < cutoff or (
        partial_date and broad_date < partial_date
    )
    financial_version = get_active_financial_version(db_path)
    financials_need_sync = _financial_refresh_due(financial_version)
    strategy_snapshot = get_active_snapshot_status(db_path)
    snapshots_need_sync = _snapshot_refresh_due_for_status(
        db_path, financial_version, strategy_snapshot
    )
    provenance_needed = not bool(
        financial_version
        and financial_version.get("point_in_time_ready")
        and financial_version.get("official_provenance_ready")
    )
    return {
        "running": running,
        "needs_sync": bool(
            prices_need_sync or financials_need_sync or snapshots_need_sync
        ),
        "prices_need_sync": bool(prices_need_sync),
        "financials_need_sync": bool(financials_need_sync),
        "snapshots_need_sync": bool(snapshots_need_sync),
        "investment_provenance_needed": provenance_needed,
        "legacy_research_planner_enabled": bool(
            get_config().allow_legacy_research_planner
        ),
        "trusted_local_planner_enabled": bool(
            strategy_snapshot
            and strategy_snapshot.get("user_confirmed_ready")
        ),
        "broad_price_date": broad_date,
        "latest_market_date": partial_date,
        "completed_market_session": broad_date,
        "provisional_prices": provisional or {
            "rows": 0, "symbols": 0, "latest": None
        },
        "source_health": source_health,
        "fallback_available": any(
            row["source"] == "KBS"
            and row["capability"] == "prices"
            and bool(row["available"])
            for row in source_health
        ),
        "fallback_mode": "comparison_only",
        "config_state": get_strategy_config_state(),
        "financial_version": financial_version,
        "strategy_snapshot": strategy_snapshot,
        "last_run": latest,
    }


def _financial_refresh_due(
    version: dict | None,
    *,
    today: dt.date | None = None,
) -> bool:
    """Return vendor-data freshness, independent of strict-PIT readiness.

    Documentary provenance is an investment gate, not a reason to download
    the complete vendor universe again.  Treating those states as equivalent
    previously caused every login sync to perform a needless full refresh.
    """
    if not version:
        return True
    current = today or dt.date.today()
    target_year, target_quarter = _completed_quarter(current)
    try:
        version_period = (
            int(version.get("as_of_year") or 0),
            int(version.get("as_of_quarter") or 0),
        )
    except (TypeError, ValueError):
        return True
    if version_period < (target_year, target_quarter):
        return True
    created_at = str(version.get("created_at") or "")
    try:
        created_date = dt.date.fromisoformat(created_at[:10])
    except ValueError:
        return True
    reporting_window = current.month in {1, 4, 5, 7, 8, 10, 11}
    refresh_days = 14 if reporting_window else 45
    return (current - created_date).days >= refresh_days


def _cleanup_stale_sync_state(db_path: Path, _run_id: int) -> None:
    """Remove resumable staging rows that no active run can consume."""
    with connect_rw(db_path) as conn:
        conn.execute("DELETE FROM financial_ratios_staging")
        conn.execute("DELETE FROM financial_sync_symbols")


def _snapshot_refresh_due(
    config: AppConfig, financial_version: dict | None
) -> bool:
    if not financial_version:
        return True
    snapshot = get_active_snapshot_status(config.db_path)
    if not snapshot:
        return True
    if int(snapshot["financial_data_version_id"]) != int(
        financial_version["id"]
    ):
        return True
    _, config_hash = strategy_config_fingerprint(config)
    if snapshot["config_hash"] != config_hash:
        return True
    today = dt.date.today()
    hold_year = today.year
    if f"{hold_year}-{config.strategy.rebalance_month:02d}-01" > today.isoformat():
        hold_year -= 1
    with connect(config.db_path) as conn:
        row = fetch_one(
            conn,
            """SELECT COUNT(*) AS cycles
               FROM strategy_cycle_snapshots
               WHERE snapshot_set_id = ? AND hold_year = ?""",
            (snapshot["snapshot_set_id"], hold_year),
        )
    expected = len(config.strategy.select_pcts) * len(STRATEGY_PARAMS)
    return int((row or {}).get("cycles") or 0) < expected


def _snapshot_refresh_due_for_status(
    db_path: Path,
    financial_version: dict | None,
    snapshot: dict | None,
) -> bool:
    if not financial_version or not snapshot:
        return True
    if not (
        bool(financial_version.get("point_in_time_ready"))
        and bool(financial_version.get("official_provenance_ready"))
    ):
        # A normal vendor sync cannot resolve missing documentary evidence.
        # Surface the provenance blocker separately instead of promising that
        # another automatic sync can rebuild an investment-ready snapshot.
        return False
    if int(snapshot["financial_data_version_id"]) != int(
        financial_version["id"]
    ):
        return True
    try:
        config = get_config()
        if config.db_path.resolve() == db_path.resolve():
            _, config_hash = strategy_config_fingerprint(config)
            return snapshot["config_hash"] != config_hash
    except (OSError, RuntimeError):
        pass
    return False
