"""Immutable strategy-cycle snapshots and two-tier backtests."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from ..config import AppConfig
from ..data.financial_snapshot import (
    POINT_IN_TIME_METHODOLOGY,
    get_active_financial_version,
)
from ..data.updater import _ts_to_date
from ..data.vci_client import VCIClient
from ..database.connection import connect, connect_rw, fetch_all, fetch_one
from ..strategy.position_sizer import query_adv_20d_historical
from ..strategy.signal_pe_ttm_20q import (
    PE20QCandidate,
    _latest_quarter,
    generate_signal_20q,
    select_top_n_20q,
)
from ..strategy.variants import STRATEGY_PARAMS, signal_params
from .market_data import (
    first_prices_on_or_after,
    opens_on_date,
    prices_on_date,
    strategy_timing,
    verified_benchmark_total_return_pair,
)
from .corporate_actions import CorporateActionError, build_verified_ledger
from .trusted_local import (
    TRUSTED_LOCAL,
    TRUSTED_LOCAL_METHODOLOGY,
    TRUSTED_LOCAL_PIT_POLICY,
    get_active_trusted_local_attestation,
)

SNAPSHOT_BACKTEST_CAPITAL_VND = 5_000_000_000.0
PIT_POLICY = "two_tier_v1"
STRICT_PIT = "strict_pit"
LEGACY_RESEARCH = "legacy_research"
ADJUSTED_BASIS = "vci_adjusted_total_return"
UNVERIFIED_PRICE_BASIS = "unverified_raw_price"
VERIFIED_LEDGER_BASIS = "verified_corporate_action_ledger_v1"


class StrategySnapshotError(RuntimeError):
    """Raised when a complete strategy snapshot set cannot be built."""


@dataclass(frozen=True)
class SnapshotCycleBuild:
    strategy: str
    select_pct: float
    formation_year: int
    hold_year: int
    rebalance_date: str
    signal_cutoff: str
    signal_price_date: str
    execution_date: str
    quarter_count: int
    pit_tier: str
    universe_count: int
    selected: list[PE20QCandidate]
    rebalance_prices: dict[str, dict[str, Any]]
    adv_shares: dict[str, float]
    data_checksum: str
    excluded_reason: str | None = None


def build_and_activate_snapshot_set(
    config: AppConfig,
    *,
    formation_years: Iterable[int] | None = None,
    strategies: Iterable[str] | None = None,
    select_pcts: Iterable[float] | None = None,
    capital_vnd: float = SNAPSHOT_BACKTEST_CAPITAL_VND,
    adjusted_price_client: VCIClient | None = None,
    require_adjusted_prices: bool = False,
    pending_config_version_id: int | None = None,
) -> dict[str, Any]:
    """Build strict-PIT live cycles and a separately labelled 10y study.

    The current live cycle is always sourced from ``strict_pit``.  Historical
    rows that predate verified publication timestamps are persisted in the
    separate ``legacy_research`` tables and never contribute to strict CAGR.
    """
    financial_version = get_active_financial_version(config.db_path)
    if not financial_version:
        raise StrategySnapshotError(
            "No active atomic financial-data version is available"
        )
    if (
        not bool(financial_version.get("point_in_time_ready"))
        or not bool(
            financial_version.get("official_provenance_ready")
        )
    ):
        raise StrategySnapshotError(
            "Active financial data has no fully classified official "
            "provenance version. Import and promote a reviewed official "
            "manifest first."
        )

    active_hold_year = _active_hold_year(config.strategy.rebalance_month)
    use_default_years = formation_years is None
    # Ten completed annual periods plus the live period.
    years = sorted(
        set(
            formation_years
            if formation_years is not None
            else range(active_hold_year - 11, active_hold_year)
        )
    )
    strategy_names = list(strategies or STRATEGY_PARAMS.keys())
    pcts = sorted(
        {float(value) for value in (select_pcts or config.strategy.select_pcts)}
    )
    if not years:
        raise StrategySnapshotError("At least one formation year is required")

    strict_builds = _build_cycles(
        config,
        years,
        strategy_names,
        pcts,
        financial_version,
        STRICT_PIT,
    )
    research_builds = _build_cycles(
        config,
        years,
        strategy_names,
        pcts,
        financial_version,
        LEGACY_RESEARCH,
    )

    if use_default_years:
        invalid_live = [
            build
            for build in strict_builds
            if build.hold_year == active_hold_year
            and (
                build.excluded_reason
                or len(build.selected) < config.strategy.min_holdings
            )
        ]
        if invalid_live:
            details = "; ".join(
                f"{item.strategy}/{item.select_pct:g}%: "
                f"{item.excluded_reason or 'fewer than 15 stocks'}"
                for item in invalid_live
            )
            raise StrategySnapshotError(
                "The current strict-PIT cycle is incomplete: " + details
            )

    completed_builds = [
        build
        for build in (*strict_builds, *research_builds)
        if build.selected and not build.excluded_reason
    ]
    histories, research_price_basis = _performance_histories(
        config,
        completed_builds,
        adjusted_price_client,
        require_adjusted_prices=require_adjusted_prices,
    )
    strict_price_basis = research_price_basis
    if (
        _corporate_action_coverage_complete(config.db_path, strict_builds)
        and _benchmark_total_return_coverage_complete(
            config, strict_builds
        )
    ):
        strict_price_basis = VERIFIED_LEDGER_BASIS
    strict_backtests = _run_snapshot_backtests(
        config,
        strict_builds,
        capital_vnd,
        histories,
        strict_price_basis,
    )
    research_backtests = _run_snapshot_backtests(
        config,
        research_builds,
        capital_vnd,
        histories,
        research_price_basis,
    )
    backtests = strict_backtests + research_backtests
    live_strict_builds = [
        build
        for build in strict_builds
        if build.hold_year == active_hold_year
    ]
    execution_price_basis = _execution_price_provenance(
        config.db_path, live_strict_builds
    )
    signal_price_basis = _signal_price_provenance(
        config.db_path, live_strict_builds
    )
    canonical_price_source_ready = _price_sources_consistent(
        config.db_path, live_strict_builds
    )
    official_provenance_ready = _strict_builds_have_official_provenance(
        config.db_path, live_strict_builds
    )
    live_corporate_actions_ready = _corporate_action_coverage_complete(
        config.db_path, live_strict_builds
    )
    portfolio_ready = (
        execution_price_basis == "verified_execution_unadjusted"
        and signal_price_basis == "verified_signal_unadjusted"
        and canonical_price_source_ready
        and official_provenance_ready
        and live_corporate_actions_ready
    )
    # Current performance is authoritative only through the explicit
    # corporate-action ledger. Vendor-adjusted prices remain a comparison
    # series and never satisfy this gate on their own.
    performance_ready = portfolio_ready
    backtest_ready = (
        strict_price_basis == VERIFIED_LEDGER_BASIS
        and _corporate_action_coverage_complete(
            config.db_path, strict_builds
        )
        and _strict_backtests_cover_ten_cycles(strict_backtests)
    )
    blocking_issues = []
    if execution_price_basis != "verified_execution_unadjusted":
        blocking_issues.append("EXECUTION_PRICE_PROVENANCE_UNVERIFIED")
    if signal_price_basis != "verified_signal_unadjusted":
        blocking_issues.append("SIGNAL_PRICE_PROVENANCE_UNVERIFIED")
    if not canonical_price_source_ready:
        blocking_issues.append("PRICE_SOURCE_MIXED_OR_UNKNOWN")
    if not official_provenance_ready:
        blocking_issues.append("OFFICIAL_FINANCIAL_PROVENANCE_INCOMPLETE")
    if not live_corporate_actions_ready:
        blocking_issues.append("CORPORATE_ACTION_COVERAGE_INCOMPLETE")
    if strict_price_basis != VERIFIED_LEDGER_BASIS:
        blocking_issues.append("BENCHMARK_TOTAL_RETURN_UNVERIFIED")
    if not backtest_ready:
        blocking_issues.append("STRICT_BACKTEST_NOT_FULLY_VERIFIED")

    config_json, config_hash = strategy_config_fingerprint(config)
    backtest_json = json.dumps(
        backtests, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )

    with connect_rw(config.db_path) as conn:
        cur = conn.execute(
            """INSERT INTO strategy_snapshot_sets
               (financial_data_version_id, config_json, config_hash,
                backtest_json, methodology_version, is_active,
                price_basis, pit_policy, execution_price_basis,
                signal_price_basis,
                lifecycle_status, portfolio_ready, performance_ready,
                backtest_ready, blocking_issues_json)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 'building', ?, ?, ?, ?)""",
            (
                financial_version["id"],
                config_json,
                config_hash,
                backtest_json,
                POINT_IN_TIME_METHODOLOGY,
                strict_price_basis,
                PIT_POLICY,
                execution_price_basis,
                signal_price_basis,
                int(portfolio_ready),
                int(performance_ready),
                int(backtest_ready),
                json.dumps(blocking_issues, separators=(",", ":")),
            ),
        )
        set_id = int(cur.lastrowid)
        _persist_strict_cycles(
            conn, set_id, strict_builds, strict_price_basis
        )
        _persist_research_cycles(
            conn, set_id, research_builds, research_price_basis
        )
        _persist_backtests(conn, set_id, backtests)

        _activate_snapshot_and_config(
            conn,
            set_id,
            pending_config_version_id=pending_config_version_id,
        )

    return {
        "snapshot_set_id": set_id,
        "financial_data_version_id": int(financial_version["id"]),
        "financial_content_hash": financial_version["content_hash"],
        "methodology_version": POINT_IN_TIME_METHODOLOGY,
        "pit_policy": PIT_POLICY,
        "price_basis": strict_price_basis,
        "execution_price_basis": execution_price_basis,
        "signal_price_basis": signal_price_basis,
        "config_hash": config_hash,
        "cycle_count": len(strict_builds),
        "research_cycle_count": len(research_builds),
        "backtests": backtests,
    }


def build_and_activate_trusted_local_snapshot_set(
    config: AppConfig,
    *,
    formation_years: Iterable[int] | None = None,
    strategies: Iterable[str] | None = None,
    select_pcts: Iterable[float] | None = None,
    capital_vnd: float = SNAPSHOT_BACKTEST_CAPITAL_VND,
    adjusted_price_client: VCIClient | None = None,
    require_adjusted_prices: bool = True,
    pending_config_version_id: int | None = None,
) -> dict[str, Any]:
    """Build an immutable snapshot from owner-confirmed local data.

    This is deliberately not strict PIT. It freezes the inputs used by the
    planner while preserving a distinct trust label in the API, UI and CSV.
    """
    financial_version = get_active_financial_version(config.db_path)
    if not financial_version:
        raise StrategySnapshotError(
            "No active atomic financial-data version is available"
        )
    attestation = get_active_trusted_local_attestation(config.db_path)
    if not attestation:
        raise StrategySnapshotError(
            "The existing local database has not been confirmed by the owner"
        )
    if int(attestation["financial_data_version_id"]) != int(
        financial_version["id"]
    ) or str(attestation["financial_content_hash"]) != str(
        financial_version["content_hash"]
    ):
        raise StrategySnapshotError(
            "The active financial dataset changed after the owner "
            "confirmation; create a new backup and confirmation first"
        )

    active_hold_year = _active_hold_year(config.strategy.rebalance_month)
    use_default_years = formation_years is None
    years = sorted(
        set(
            formation_years
            if formation_years is not None
            else range(active_hold_year - 11, active_hold_year)
        )
    )
    strategy_names = list(strategies or STRATEGY_PARAMS.keys())
    pcts = sorted(
        {float(value) for value in (select_pcts or config.strategy.select_pcts)}
    )
    if not years:
        raise StrategySnapshotError("At least one formation year is required")

    builds = _build_cycles(
        config,
        years,
        strategy_names,
        pcts,
        financial_version,
        TRUSTED_LOCAL,
    )
    if use_default_years:
        invalid_live = [
            build
            for build in builds
            if build.hold_year == active_hold_year
            and (
                build.excluded_reason
                or len(build.selected) < config.strategy.min_holdings
            )
        ]
        if invalid_live:
            details = "; ".join(
                f"{item.strategy}/{item.select_pct:g}%: "
                f"{item.excluded_reason or 'fewer than 15 stocks'}"
                for item in invalid_live
            )
            raise StrategySnapshotError(
                "The current owner-confirmed cycle is incomplete: " + details
            )

    completed_builds = [
        build
        for build in builds
        if build.selected and not build.excluded_reason
    ]
    histories, price_basis = _performance_histories(
        config,
        completed_builds,
        adjusted_price_client,
        require_adjusted_prices=require_adjusted_prices,
    )
    if price_basis != ADJUSTED_BASIS:
        raise StrategySnapshotError(
            "Owner-confirmed mode requires complete adjusted price histories"
        )
    backtests = _run_snapshot_backtests(
        config, builds, capital_vnd, histories, price_basis
    )
    if use_default_years:
        expected = {
            (strategy, pct) for strategy in strategy_names for pct in pcts
        }
        complete = {
            (str(row["strategy"]), float(row["select_pct"]))
            for row in backtests
            if int(row["cycle_count"]) >= 10
        }
        missing = sorted(expected - complete)
        if missing:
            raise StrategySnapshotError(
                "The 10-year local-data backtest is incomplete for: "
                + ", ".join(
                    f"{strategy}/{pct:g}%" for strategy, pct in missing
                )
            )

    config_json, config_hash = strategy_config_fingerprint(
        config,
        methodology_version=TRUSTED_LOCAL_METHODOLOGY,
        pit_policy=TRUSTED_LOCAL_PIT_POLICY,
    )
    backtest_json = json.dumps(
        backtests, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    with connect_rw(config.db_path) as conn:
        cur = conn.execute(
            """INSERT INTO strategy_snapshot_sets
               (financial_data_version_id, config_json, config_hash,
                backtest_json, methodology_version, is_active,
                price_basis, pit_policy, execution_price_basis,
                signal_price_basis, lifecycle_status, portfolio_ready,
                performance_ready, backtest_ready, blocking_issues_json,
                trusted_local_ready, trusted_local_attestation_id)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 'building',
                       0, 0, 0, '[]', 1, ?)""",
            (
                financial_version["id"],
                config_json,
                config_hash,
                backtest_json,
                TRUSTED_LOCAL_METHODOLOGY,
                price_basis,
                TRUSTED_LOCAL_PIT_POLICY,
                "user_confirmed_local_unadjusted",
                "user_confirmed_local_unadjusted",
                int(attestation["id"]),
            ),
        )
        set_id = int(cur.lastrowid)
        _persist_strict_cycles(conn, set_id, builds, price_basis)
        _persist_backtests(conn, set_id, backtests)
        _activate_snapshot_and_config(
            conn,
            set_id,
            pending_config_version_id=pending_config_version_id,
        )

    return {
        "snapshot_set_id": set_id,
        "financial_data_version_id": int(financial_version["id"]),
        "financial_content_hash": financial_version["content_hash"],
        "methodology_version": TRUSTED_LOCAL_METHODOLOGY,
        "pit_policy": TRUSTED_LOCAL_PIT_POLICY,
        "price_basis": price_basis,
        "execution_price_basis": "user_confirmed_local_unadjusted",
        "signal_price_basis": "user_confirmed_local_unadjusted",
        "config_hash": config_hash,
        "cycle_count": len(builds),
        "research_cycle_count": 0,
        "attestation_id": int(attestation["id"]),
        "attestation_hash": attestation["attestation_hash"],
        "backtests": backtests,
    }


def _activate_snapshot_and_config(
    conn: sqlite3.Connection,
    snapshot_set_id: int,
    *,
    pending_config_version_id: int | None,
) -> None:
    """Atomically switch the snapshot and its pending strategy configuration."""
    snapshot_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(strategy_snapshot_sets)")
    }
    trusted_expression = (
        "trusted_local_ready"
        if "trusted_local_ready" in snapshot_columns
        else "0 AS trusted_local_ready"
    )
    readiness = conn.execute(
        f"""SELECT portfolio_ready, {trusted_expression}
            FROM strategy_snapshot_sets WHERE id = ?""",
        (snapshot_set_id,),
    ).fetchone()
    if not readiness or not (bool(readiness[0]) or bool(readiness[1])):
        raise StrategySnapshotError(
            "Snapshot chưa đạt cổng portfolio_ready; "
            "không được phép kích hoạt"
        )
    if pending_config_version_id is not None:
        pending = conn.execute(
            """SELECT id FROM strategy_config_versions
               WHERE id = ? AND status = 'pending'""",
            (pending_config_version_id,),
        ).fetchone()
        if pending is None:
            raise StrategySnapshotError(
                "Pending strategy configuration changed while snapshot was building"
            )

        conn.execute(
            """UPDATE strategy_config_versions
               SET status = 'failed',
                   error = 'Replaced by a newly activated configuration'
               WHERE status = 'active'"""
        )
        updated = conn.execute(
            """UPDATE strategy_config_versions
               SET status = 'active', activated_at = CURRENT_TIMESTAMP,
                   error = NULL
               WHERE id = ? AND status = 'pending'""",
            (pending_config_version_id,),
        )
        if updated.rowcount != 1:
            raise StrategySnapshotError(
                "Could not activate pending strategy configuration"
            )

    conn.execute(
        "UPDATE strategy_snapshot_sets SET is_active = 0 WHERE is_active = 1"
    )
    activated = conn.execute(
        """UPDATE strategy_snapshot_sets
           SET is_active = 1, lifecycle_status = 'active',
               validated_at = CURRENT_TIMESTAMP,
               activated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (snapshot_set_id,),
    )
    if activated.rowcount != 1:
        raise StrategySnapshotError("Could not activate strategy snapshot set")


def _build_cycles(
    config: AppConfig,
    formation_years: list[int],
    strategies: list[str],
    pcts: list[float],
    financial_version: dict[str, Any],
    pit_tier: str,
) -> list[SnapshotCycleBuild]:
    builds: list[SnapshotCycleBuild] = []
    for strategy in strategies:
        if strategy not in STRATEGY_PARAMS:
            raise StrategySnapshotError(f"Unknown strategy: {strategy}")
        max_quarters = int(STRATEGY_PARAMS[strategy]["max_quarters"])
        for formation_year in formation_years:
            hold_year = formation_year + 1
            rebalance_date = (
                f"{hold_year}-{config.strategy.rebalance_month:02d}-01"
            )
            try:
                timing = strategy_timing(
                    config.db_path,
                    rebalance_date,
                    config.strategy.benchmark_symbol,
                )
            except ValueError as exc:
                raise StrategySnapshotError(str(exc)) from exc
            quarter_count, candidates = _signal_for_available_window(
                config,
                strategy,
                formation_year,
                hold_year,
                rebalance_date,
                timing["signal_cutoff"],
                timing["signal_price_date"],
                max_quarters,
                financial_version,
                pit_tier,
            )
            universe_count = len(candidates)
            base_reason = None
            if universe_count < config.strategy.min_holdings:
                base_reason = (
                    f"only {universe_count} eligible stocks with "
                    f"{quarter_count} available quarters"
                )
            for pct in pcts:
                selected = (
                    select_top_n_20q(
                        candidates,
                        pct,
                        min_holdings=config.strategy.min_holdings,
                    )
                    if base_reason is None
                    else []
                )
                prices: dict[str, dict[str, Any]] = {}
                adv: dict[str, float] = {}
                if selected:
                    prices, adv = _snapshot_market_inputs(
                        config, selected, timing["execution_date"]
                    )
                official_provenance: dict[str, dict[str, Any]] = {}
                if pit_tier == STRICT_PIT and selected:
                    with connect(config.db_path) as provenance_conn:
                        official_provenance = {
                            candidate.symbol: _candidate_official_provenance(
                                provenance_conn,
                                candidate.symbol,
                                timing["signal_cutoff"],
                                timing["signal_price_date"],
                                quarter_count,
                                timing["execution_date"],
                            )
                            for candidate in selected
                        }
                checksum = _cycle_checksum(
                    strategy,
                    pct,
                    hold_year,
                    timing["signal_cutoff"],
                    timing["signal_price_date"],
                    timing["execution_date"],
                    quarter_count,
                    pit_tier,
                    selected,
                    prices,
                    adv,
                    official_provenance,
                    str(financial_version["content_hash"]),
                )
                builds.append(
                    SnapshotCycleBuild(
                        strategy=strategy,
                        select_pct=pct,
                        formation_year=formation_year,
                        hold_year=hold_year,
                        rebalance_date=rebalance_date,
                        signal_cutoff=timing["signal_cutoff"],
                        signal_price_date=timing["signal_price_date"],
                        execution_date=timing["execution_date"],
                        quarter_count=quarter_count,
                        pit_tier=pit_tier,
                        universe_count=universe_count,
                        selected=selected,
                        rebalance_prices=prices,
                        adv_shares=adv,
                        data_checksum=checksum,
                        excluded_reason=base_reason,
                    )
                )
    return builds


def _signal_for_available_window(
    config: AppConfig,
    strategy: str,
    formation_year: int,
    hold_year: int,
    rebalance_date: str,
    signal_cutoff: str,
    signal_price_date: str,
    max_quarters: int,
    financial_version: dict[str, Any],
    pit_tier: str,
) -> tuple[int, list[PE20QCandidate]]:
    quarter_count = _available_quarter_window(
        config.db_path,
        hold_year,
        config.strategy.rebalance_month,
        max_quarters,
        config.strategy.min_holdings,
        pit_tier,
        signal_cutoff,
        int(financial_version["id"]),
    )
    if quarter_count <= 0:
        return 0, []
    kwargs = signal_params(strategy)
    if strategy == "LAST_8Q_PLUS":
        kwargs["require_last_n_positive"] = min(8, quarter_count)
    if pit_tier == STRICT_PIT:
        kwargs.update(
            as_of_date=signal_cutoff,
            financial_data_version_id=int(financial_version["id"]),
            require_official_provenance=True,
        )
    candidates = generate_signal_20q(
        config.db_path,
        formation_year,
        config,
        hold_year=hold_year,
        rebalance_date=rebalance_date,
        signal_price_date=signal_price_date,
        rebalance_month=config.strategy.rebalance_month,
        quarter_count=quarter_count,
        **kwargs,
    )
    return quarter_count, candidates


def _available_quarter_window(
    db_path: Path,
    hold_year: int,
    rebalance_month: int,
    max_quarters: int,
    min_holdings: int,
    pit_tier: str,
    signal_cutoff: str,
    financial_version_id: int,
) -> int:
    end_year, end_quarter = _latest_quarter(hold_year, rebalance_month)
    with connect(db_path) as conn:
        if pit_tier == STRICT_PIT:
            rows = fetch_all(
                conn,
                """SELECT DISTINCT f.symbol, f.year, f.quarter
                   FROM financial_period_facts f
                   JOIN financial_filing_revisions r
                     ON r.id = f.filing_revision_id
                   WHERE r.verification_status = 'verified'
                     AND r.available_at <= ?
                     AND f.is_independent_quarter = 1
                     AND f.basic_eps_vnd IS NOT NULL
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
                                 AND resolution.verification_status =
                                     'verified'
                                 AND resolution.available_at <= ?
                           )
                     )
                     AND (f.year < ? OR (f.year = ? AND f.quarter <= ?))""",
                (
                    signal_cutoff,
                    signal_cutoff,
                    signal_cutoff,
                    end_year,
                    end_year,
                    end_quarter,
                ),
            )
        else:
            rows = fetch_all(
                conn,
                """SELECT DISTINCT symbol, year, quarter
                   FROM financial_ratios
                   WHERE quarter BETWEEN 1 AND 4
                     AND eps_vnd IS NOT NULL
                     AND (year < ? OR (year = ? AND quarter <= ?))""",
                (end_year, end_year, end_quarter),
            )
    by_symbol: dict[str, set[tuple[int, int]]] = {}
    for row in rows:
        by_symbol.setdefault(row["symbol"], set()).add(
            (int(row["year"]), int(row["quarter"]))
        )
    available = {
        period for periods in by_symbol.values() for period in periods
    }
    count = 0
    year, quarter = end_year, end_quarter
    while count < max_quarters and (year, quarter) in available:
        count += 1
        quarter -= 1
        if quarter == 0:
            year -= 1
            quarter = 4
    periods: list[tuple[int, int]] = []
    year, quarter = end_year, end_quarter
    for _ in range(count):
        periods.append((year, quarter))
        quarter -= 1
        if quarter == 0:
            year -= 1
            quarter = 4
    for window in range(count, 0, -1):
        required = set(periods[:window])
        complete_symbols = sum(
            required.issubset(symbol_periods)
            for symbol_periods in by_symbol.values()
        )
        if complete_symbols >= min_holdings:
            return window
    return 0


def _cycle_checksum(
    strategy: str,
    select_pct: float,
    hold_year: int,
    signal_cutoff: str,
    signal_price_date: str,
    execution_date: str,
    quarter_count: int,
    pit_tier: str,
    selected: list[PE20QCandidate],
    prices: dict[str, dict[str, Any]],
    adv: dict[str, float],
    official_provenance: dict[str, dict[str, Any]],
    financial_hash: str,
) -> str:
    payload = {
        "strategy": strategy,
        "select_pct": select_pct,
        "hold_year": hold_year,
        "signal_cutoff": signal_cutoff,
        "signal_price_date": signal_price_date,
        "execution_date": execution_date,
        "quarter_count": quarter_count,
        "pit_tier": pit_tier,
        "financial_hash": financial_hash,
        "items": [
            {
                "symbol": item.symbol,
                "rank": item.signal_rank,
                "eps": item.avg_eps_20q,
                "pe": item.pe_ttm_20q,
                "signal_price_vnd": item.buy_price_vnd,
                "execution": prices.get(item.symbol),
                "adv": adv.get(item.symbol),
                "official_provenance": official_provenance.get(item.symbol),
            }
            for item in selected
        ],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _candidate_official_provenance(
    conn: sqlite3.Connection,
    symbol: str,
    signal_cutoff: str,
    signal_price_date: str,
    quarter_count: int,
    execution_date: str,
) -> dict[str, Any]:
    revisions = [
        int(row[0])
        for row in conn.execute(
            """WITH ranked AS (
                   SELECT r.id, f.year, f.quarter,
                          ROW_NUMBER() OVER (
                              PARTITION BY f.year, f.quarter
                              ORDER BY
                                       CASE r.statement_scope
                                         WHEN 'consolidated' THEN 0
                                         ELSE 1
                                       END,
                                       r.available_at DESC,
                                       r.revision_number DESC, r.id DESC
                          ) AS revision_rank
                   FROM financial_period_facts f
                   JOIN financial_filing_revisions r
                     ON r.id = f.filing_revision_id
                   WHERE f.symbol = ?
                     AND r.verification_status = 'verified'
                     AND f.is_independent_quarter = 1
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
                                 AND resolution.verification_status =
                                     'verified'
                                 AND resolution.available_at <= ?
                           )
                     )
               )
               SELECT id FROM ranked
               WHERE revision_rank = 1
               ORDER BY year DESC, quarter DESC
               LIMIT ?""",
            (
                symbol,
                signal_cutoff,
                signal_cutoff,
                signal_cutoff,
                quarter_count,
            ),
        )
    ]
    shares_row = conn.execute(
        """SELECT shares_outstanding
           FROM shares_outstanding_history
           WHERE symbol = ?
             AND verification_status = 'verified'
             AND effective_from <= ?
             AND (effective_to IS NULL OR effective_to >= ?)
           ORDER BY effective_from DESC, id DESC LIMIT 1""",
        (symbol, signal_price_date, signal_price_date),
    ).fetchone()
    shares = float(shares_row[0]) if shares_row else None
    price_provenance: dict[str, dict[str, Any] | None] = {}
    for label, price_date in (
        ("signal", signal_price_date),
        ("execution", execution_date),
    ):
        metadata = fetch_one(
            conn,
            """SELECT source, price_basis, raw_unit, is_provisional,
                      observed_at, source_url, source_payload_sha256
               FROM market_price_metadata
               WHERE symbol = ? AND price_date = ?""",
            (symbol, price_date),
        )
        price_provenance[label] = metadata
    return {
        "symbol": symbol,
        "revisions": revisions,
        "shares": shares,
        "signal_date": signal_price_date,
        "execution_date": execution_date,
        "price_provenance": price_provenance,
    }


def strategy_config_fingerprint(
    config: AppConfig,
    *,
    methodology_version: str = POINT_IN_TIME_METHODOLOGY,
    pit_policy: str = PIT_POLICY,
) -> tuple[str, str]:
    config_json = json.dumps(
        {
            "strategy": asdict(config.strategy),
            # Code-level signal semantics are part of the immutable snapshot
            # identity. A strategy-definition change must force a rebuild even
            # when the user-facing configuration is unchanged.
            "strategy_variants": STRATEGY_PARAMS,
            "signal_methodology": methodology_version,
            "pit_policy": pit_policy,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        config_json,
        hashlib.sha256(config_json.encode("utf-8")).hexdigest(),
    )


def load_active_cycle_snapshot(
    db_path: Path,
    strategy: str,
    select_pct: float,
    hold_year: int,
) -> dict[str, Any] | None:
    """Load a valid strict-PIT cycle from the active immutable set."""
    with connect(db_path) as conn:
        cycle = fetch_one(
            conn,
            """SELECT c.*, s.financial_data_version_id, s.config_json,
                      s.config_hash,
                      s.methodology_version, s.price_basis, s.pit_policy,
                      s.execution_price_basis,
                      s.signal_price_basis,
                      s.created_at AS snapshot_set_created_at,
                      f.content_hash AS financial_content_hash
               FROM strategy_cycle_snapshots c
               JOIN strategy_snapshot_sets s ON s.id = c.snapshot_set_id
               JOIN financial_data_versions f
                 ON f.id = s.financial_data_version_id
               WHERE s.is_active = 1
                 AND s.lifecycle_status = 'active'
                 AND s.portfolio_ready = 1
                 AND c.strategy = ?
                 AND c.select_pct = ?
                 AND c.hold_year = ?
                 AND c.pit_tier = 'strict_pit'
                 AND c.excluded_reason IS NULL
                 AND c.selected_count >= 15
               LIMIT 1""",
            (strategy, float(select_pct), hold_year),
        )
        if not cycle:
            return None
        items = fetch_all(
            conn,
            """SELECT * FROM strategy_cycle_snapshot_items
               WHERE cycle_snapshot_id = ?
               ORDER BY signal_rank""",
            (cycle["id"],),
        )
    return {**cycle, "items": items}


def load_trusted_local_cycle_snapshot(
    db_path: Path,
    strategy: str,
    select_pct: float,
    hold_year: int,
) -> dict[str, Any] | None:
    """Load an active immutable cycle confirmed from the local database."""
    with connect(db_path) as conn:
        cycle = fetch_one(
            conn,
            """SELECT c.*, s.financial_data_version_id, s.config_json,
                      s.config_hash, s.methodology_version, s.price_basis,
                      s.pit_policy, s.execution_price_basis,
                      s.signal_price_basis,
                      s.created_at AS snapshot_set_created_at,
                      f.content_hash AS financial_content_hash,
                      a.attestation_hash,
                      'trusted_local' AS trust_tier
               FROM strategy_cycle_snapshots c
               JOIN strategy_snapshot_sets s ON s.id = c.snapshot_set_id
               JOIN financial_data_versions f
                 ON f.id = s.financial_data_version_id
               JOIN trusted_local_attestations a
                 ON a.id = s.trusted_local_attestation_id
               WHERE s.is_active = 1
                 AND s.lifecycle_status = 'active'
                 AND s.trusted_local_ready = 1
                 AND a.is_active = 1
                 AND a.revoked_at IS NULL
                 AND c.strategy = ?
                 AND c.select_pct = ?
                 AND c.hold_year = ?
                 AND c.pit_tier = 'trusted_local'
                 AND c.excluded_reason IS NULL
                 AND c.selected_count >= 15
               LIMIT 1""",
            (strategy, float(select_pct), hold_year),
        )
        if not cycle:
            return None
        items = fetch_all(
            conn,
            """SELECT * FROM strategy_cycle_snapshot_items
               WHERE cycle_snapshot_id = ?
               ORDER BY signal_rank""",
            (cycle["id"],),
        )
    if len(items) < 15:
        return None
    return {**cycle, "items": items}


def load_legacy_research_cycle_snapshot(
    db_path: Path,
    strategy: str,
    select_pct: float,
    hold_year: int,
) -> dict[str, Any] | None:
    """Load the newest explicitly quarantined vendor cycle for research.

    This is intentionally separate from ``load_active_cycle_snapshot``.  It
    does not alter lifecycle/readiness fields and cannot make a legacy
    snapshot appear strict-PIT or investment-ready.
    """
    with connect(db_path) as conn:
        cycle = fetch_one(
            conn,
            """SELECT c.*, s.financial_data_version_id, s.config_json,
                      s.config_hash,
                      s.methodology_version, s.price_basis, s.pit_policy,
                      s.execution_price_basis, s.signal_price_basis,
                      s.created_at AS snapshot_set_created_at,
                      f.content_hash AS financial_content_hash,
                      'legacy_research' AS trust_tier
               FROM strategy_cycle_snapshots c
               JOIN strategy_snapshot_sets s ON s.id = c.snapshot_set_id
               JOIN financial_data_versions f
                 ON f.id = s.financial_data_version_id
               WHERE s.is_active = 1
                 AND s.lifecycle_status = 'quarantined'
                 AND s.portfolio_ready = 0
                 AND s.execution_price_basis = 'legacy_unknown'
                 AND s.signal_price_basis = 'legacy_unknown'
                 AND c.strategy = ?
                 AND c.select_pct = ?
                 AND c.hold_year = ?
                 AND c.excluded_reason IS NULL
                 AND c.selected_count >= 15
               ORDER BY s.id DESC, c.id DESC
               LIMIT 1""",
            (strategy, float(select_pct), hold_year),
        )
        if not cycle:
            return None
        items = fetch_all(
            conn,
            """SELECT * FROM strategy_cycle_snapshot_items
               WHERE cycle_snapshot_id = ?
               ORDER BY signal_rank""",
            (cycle["id"],),
        )
    if len(items) < 15:
        return None
    return {**cycle, "items": items}


def get_active_snapshot_status(db_path: Path) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        snapshot = fetch_one(
            conn,
            """SELECT s.id AS snapshot_set_id, s.financial_data_version_id,
                      s.config_hash, s.created_at, s.activated_at,
                      s.methodology_version, s.price_basis, s.pit_policy,
                      s.execution_price_basis,
                      s.signal_price_basis,
                      s.lifecycle_status, s.portfolio_ready,
                      s.performance_ready, s.backtest_ready,
                      s.trusted_local_ready,
                      s.trusted_local_attestation_id,
                      s.blocking_issues_json, s.validation_report_hash,
                      s.validated_at,
                      f.content_hash AS financial_content_hash,
                      f.source, f.source_api, f.point_in_time_ready,
                      f.publication_coverage_pct, f.verified_row_count,
                      a.attestation_hash AS trusted_local_attestation_hash,
                      a.attested_at AS trusted_local_attested_at,
                      a.attested_by AS trusted_local_attested_by,
                      a.is_active AS trusted_local_attestation_active,
                      a.revoked_at AS trusted_local_revoked_at
               FROM strategy_snapshot_sets s
               JOIN financial_data_versions f
                 ON f.id = s.financial_data_version_id
               LEFT JOIN trusted_local_attestations a
                 ON a.id = s.trusted_local_attestation_id
               WHERE s.is_active = 1
               ORDER BY s.id DESC LIMIT 1""",
        )
        if not snapshot:
            return None
        counts = fetch_one(
            conn,
            """SELECT COUNT(*) AS cycles,
                      SUM(CASE WHEN excluded_reason IS NULL
                               AND selected_count >= 15 THEN 1 ELSE 0 END)
                          AS valid_cycles,
                      MIN(CASE WHEN excluded_reason IS NULL
                               AND selected_count >= 15
                               THEN hold_year END) AS first_valid_hold_year,
                      MAX(CASE WHEN excluded_reason IS NULL
                               AND selected_count >= 15
                               THEN hold_year END) AS last_valid_hold_year
               FROM strategy_cycle_snapshots
               WHERE snapshot_set_id = ?""",
            (snapshot["snapshot_set_id"],),
        )
        research_count = fetch_one(
            conn,
            """SELECT COUNT(*) AS cycles
               FROM strategy_research_cycles
               WHERE snapshot_set_id = ?""",
            (snapshot["snapshot_set_id"],),
        )
        research_planner_cycles = fetch_all(
            conn,
            """SELECT c.strategy, c.select_pct, c.hold_year,
                      c.selected_count
               FROM strategy_cycle_snapshots c
               JOIN strategy_snapshot_sets s ON s.id = c.snapshot_set_id
               WHERE s.is_active = 1
                 AND s.lifecycle_status = 'quarantined'
                 AND s.portfolio_ready = 0
                 AND s.execution_price_basis = 'legacy_unknown'
                 AND s.signal_price_basis = 'legacy_unknown'
                 AND c.excluded_reason IS NULL
                 AND c.selected_count >= 15
                 AND c.hold_year = (
                     SELECT MAX(c2.hold_year)
                     FROM strategy_cycle_snapshots c2
                     WHERE c2.snapshot_set_id = s.id
                 )
               ORDER BY c.strategy, c.select_pct""",
        )
        current_pit_tier = (
            TRUSTED_LOCAL
            if bool(snapshot.get("trusted_local_ready"))
            else STRICT_PIT
        )
        current_timing = fetch_one(
            conn,
            """SELECT id, signal_cutoff, signal_price_date, execution_date,
                      universe_count, selected_count
               FROM strategy_cycle_snapshots
               WHERE snapshot_set_id = ?
                 AND pit_tier = ?
               ORDER BY hold_year DESC, select_pct ASC LIMIT 1""",
            (snapshot["snapshot_set_id"], current_pit_tier),
        )
        provenance_rows = fetch_all(
            conn,
            """SELECT verification_status, COUNT(*) AS rows,
                      COUNT(DISTINCT symbol) AS symbols
               FROM financial_filing_revisions
               GROUP BY verification_status""",
        )
        benchmark_evidence = fetch_one(
            conn,
            """SELECT
                 SUM(CASE WHEN verification_status = 'verified'
                          THEN 1 ELSE 0 END) AS verified_rows,
                 SUM(CASE WHEN verification_status = 'conflict'
                          THEN 1 ELSE 0 END) AS conflict_rows,
                 MIN(CASE WHEN verification_status = 'verified'
                          THEN price_date END) AS first_verified_date,
                 MAX(CASE WHEN verification_status = 'verified'
                          THEN price_date END) AS last_verified_date
               FROM benchmark_total_return_history
               WHERE symbol = 'VNINDEX'""",
        )
        source_failures = fetch_all(
            conn,
            """WITH latest AS (
                   SELECT symbol, status, error, observed_at,
                          required_for_investment,
                          ROW_NUMBER() OVER (
                            PARTITION BY symbol
                            ORDER BY observed_at DESC, run_id DESC
                          ) AS status_rank
                   FROM financial_sync_symbol_history
               )
               SELECT symbol, status, error, observed_at
               FROM latest
               WHERE status_rank = 1
                 AND required_for_investment = 1
                 AND status IN (
                   'source_empty', 'ingestion_missing', 'conflict', 'error'
                 )
               ORDER BY observed_at DESC, symbol LIMIT 100""",
        )
        current_price_date_row = fetch_one(
            conn,
            """SELECT price_date, COUNT(DISTINCT symbol) AS symbols
               FROM market_price_metadata
               WHERE is_provisional = 0
                 AND source <> 'LEGACY_UNKNOWN'
                 AND source_url IS NOT NULL
                 AND source_url <> ''
                 AND raw_unit = 'THOUSAND_VND'
                 AND price_basis IN (
                   'current_spot', 'execution_unadjusted'
                 )
                 AND LENGTH(source_payload_sha256) = 64
                 AND EXISTS (
                   SELECT 1
                   FROM price_source_observations observed
                   WHERE observed.symbol =
                         market_price_metadata.symbol
                     AND observed.price_date =
                         market_price_metadata.price_date
                     AND observed.source =
                         market_price_metadata.source
                     AND observed.payload_sha256 =
                         market_price_metadata.source_payload_sha256
                     AND observed.is_session_final = 1
                     AND observed.verification_status = 'verified'
                 )
                 AND NOT EXISTS (
                   SELECT 1
                   FROM price_source_observations conflict
                   WHERE conflict.symbol =
                         market_price_metadata.symbol
                     AND conflict.price_date =
                         market_price_metadata.price_date
                     AND conflict.verification_status = 'conflict'
                 )
               GROUP BY price_date
               HAVING COUNT(DISTINCT symbol) >= 100
               ORDER BY price_date DESC LIMIT 1""",
        )
        trusted_local_mode = bool(snapshot.get("trusted_local_ready"))
        if trusted_local_mode:
            current_price_date_row = fetch_one(
                conn,
                """SELECT time AS price_date,
                          COUNT(DISTINCT symbol) AS symbols
                   FROM stock_price_history
                   WHERE typeof(time) = 'text'
                   GROUP BY time
                   HAVING COUNT(DISTINCT symbol) >= 100
                   ORDER BY time DESC LIMIT 1""",
            )
        current_items = (
            fetch_all(
                conn,
                """SELECT symbol, price_provenance_json
                   FROM strategy_cycle_snapshot_items
                   WHERE cycle_snapshot_id = ?""",
                (current_timing["id"],),
            )
            if current_timing and current_timing.get("id")
            else []
        )
        current_price_date = (
            (current_price_date_row or {}).get("price_date")
        )
        dynamic_issues: list[str] = []
        expected_sources: set[str] = set()
        for item in ([] if trusted_local_mode else current_items):
            try:
                payload = json.loads(
                    item.get("price_provenance_json") or "{}"
                )
                expected_sources.update(
                    str((payload.get(label) or {}).get("source"))
                    for label in ("signal", "execution")
                    if (payload.get(label) or {}).get("source")
                )
            except (TypeError, json.JSONDecodeError):
                dynamic_issues.append("PRICE_PROVENANCE_JSON_INVALID")
        if not trusted_local_mode and len(expected_sources) != 1:
            dynamic_issues.append("PRICE_SOURCE_MIXED_OR_UNKNOWN")
        if (
            not trusted_local_mode
            and current_items
            and not current_price_date
        ):
            dynamic_issues.append("CURRENT_PRICE_SESSION_UNVERIFIED")
        if (
            not trusted_local_mode
            and current_items
            and current_price_date
            and len(expected_sources) == 1
        ):
            expected_source = next(iter(expected_sources))
            missing_current = []
            missing_actions = []
            for item in current_items:
                price = fetch_one(
                    conn,
                    """SELECT 1 AS ok FROM market_price_metadata
                       WHERE symbol = ? AND price_date = ?
                         AND source = ?
                         AND source_url IS NOT NULL
                         AND source_url <> ''
                         AND is_provisional = 0
                         AND raw_unit = 'THOUSAND_VND'
                         AND price_basis IN (
                           'current_spot', 'execution_unadjusted'
                         )
                         AND LENGTH(source_payload_sha256) = 64
                         AND EXISTS (
                           SELECT 1
                           FROM price_source_observations observed
                           WHERE observed.symbol =
                                 market_price_metadata.symbol
                             AND observed.price_date =
                                 market_price_metadata.price_date
                             AND observed.source =
                                 market_price_metadata.source
                             AND observed.payload_sha256 =
                                 market_price_metadata.source_payload_sha256
                             AND observed.is_session_final = 1
                             AND observed.verification_status = 'verified'
                         )
                         AND NOT EXISTS (
                           SELECT 1
                           FROM price_source_observations conflict
                           WHERE conflict.symbol =
                                 market_price_metadata.symbol
                             AND conflict.price_date =
                                 market_price_metadata.price_date
                             AND conflict.verification_status = 'conflict'
                         )""",
                    (
                        item["symbol"],
                        current_price_date,
                        expected_source,
                    ),
                )
                if not price:
                    missing_current.append(str(item["symbol"]))
                action_coverage = fetch_one(
                    conn,
                    """SELECT 1 AS ok FROM corporate_action_coverage
                       WHERE symbol = ?
                         AND coverage_status = 'verified'
                         AND start_date <= ?
                         AND end_date >= ?
                       LIMIT 1""",
                    (
                        item["symbol"],
                        (current_timing or {}).get("execution_date"),
                        current_price_date,
                    ),
                )
                if not action_coverage:
                    missing_actions.append(str(item["symbol"]))
            if missing_current:
                dynamic_issues.append(
                    "CURRENT_PRICE_PROVENANCE_MISSING:"
                    + ",".join(missing_current)
                )
            if missing_actions:
                dynamic_issues.append(
                    "CORPORATE_ACTION_COVERAGE_STALE:"
                    + ",".join(missing_actions)
                )
        backtest_rows = fetch_all(
            conn,
            """SELECT strategy, select_pct, pit_tier, start_hold_year,
                      end_hold_year, cycle_count, capital_vnd, net_cagr,
                      win_rate, price_basis, benchmark_symbol,
                      benchmark_cagr, yearly_json, excluded_cycles_json
               FROM strategy_backtest_results_v2
               WHERE snapshot_set_id = ?
               ORDER BY strategy, select_pct, pit_tier""",
            (snapshot["snapshot_set_id"],),
        )
        if not backtest_rows:
            backtest_rows = fetch_all(
                conn,
                """SELECT strategy, select_pct, 'strict_pit' AS pit_tier,
                          start_hold_year, end_hold_year,
                          (end_hold_year - start_hold_year + 1) AS cycle_count,
                          capital_vnd, net_cagr, win_rate,
                          'unverified_raw_price' AS price_basis,
                          'VNINDEX' AS benchmark_symbol,
                          NULL AS benchmark_cagr, yearly_json,
                          '[]' AS excluded_cycles_json
                   FROM strategy_backtest_results
                   WHERE snapshot_set_id = ?
                   ORDER BY strategy, select_pct""",
                (snapshot["snapshot_set_id"],),
            )
    backtests = [
        {
            **{
                key: value
                for key, value in row.items()
                if key not in {"yearly_json", "excluded_cycles_json"}
            },
            "yearly": json.loads(row["yearly_json"]),
            "excluded_cycles": json.loads(row["excluded_cycles_json"]),
            "authoritative": (
                row["pit_tier"] == STRICT_PIT
                and row["price_basis"] == VERIFIED_LEDGER_BASIS
                and bool(snapshot.get("backtest_ready"))
            ),
            "user_confirmed": (
                row["pit_tier"] == TRUSTED_LOCAL
                and bool(snapshot.get("trusted_local_ready"))
            ),
        }
        for row in backtest_rows
    ]
    count_data = counts or {}
    blocking_issues = json.loads(
        snapshot.get("blocking_issues_json") or "[]"
    )
    blocking_issues = list(
        dict.fromkeys([*blocking_issues, *dynamic_issues])
    )
    # The portfolio planner needs official PIT inputs and verified execution
    # prices. Performance and the 10-year strict backtest are independent
    # gates, allowing the verified current cycle to be released first.
    investment_ready = (
        bool(snapshot.get("portfolio_ready"))
        and snapshot.get("lifecycle_status") == "active"
        and not dynamic_issues
    )
    user_confirmed_ready = (
        bool(snapshot.get("trusted_local_ready"))
        and snapshot.get("lifecycle_status") == "active"
        and bool(snapshot.get("trusted_local_attestation_active"))
        and not snapshot.get("trusted_local_revoked_at")
    )
    return {
        **{
            key: value
            for key, value in snapshot.items()
            if key != "blocking_issues_json"
        },
        "investment_ready": investment_ready,
        "user_confirmed_ready": user_confirmed_ready,
        "research_planner_available": bool(research_planner_cycles),
        "research_planner_cycles": research_planner_cycles,
        "blocking_issues": blocking_issues,
        "cycle_count": int(count_data.get("cycles") or 0),
        "valid_cycle_count": int(count_data.get("valid_cycles") or 0),
        "research_cycle_count": int(
            (research_count or {}).get("cycles") or 0
        ),
        "strict_coverage": {
            "first_hold_year": count_data.get("first_valid_hold_year"),
            "last_hold_year": count_data.get("last_valid_hold_year"),
        },
        "signal_cutoff": (current_timing or {}).get("signal_cutoff"),
        "signal_price_date": (
            current_timing or {}
        ).get("signal_price_date"),
        "execution_date": (current_timing or {}).get("execution_date"),
        "current_verified_price_date": current_price_date,
        "universe_coverage": {
            "eligible": (current_timing or {}).get("universe_count"),
            "selected": (current_timing or {}).get("selected_count"),
        },
        "provenance_coverage": provenance_rows,
        "source_blockers": source_failures,
        "benchmark_status": (
            "verified_total_return"
            if bool(snapshot.get("backtest_ready"))
            else "user_confirmed_vendor_adjusted"
            if user_confirmed_ready
            else "unverified"
        ),
        "benchmark_total_return_coverage": benchmark_evidence or {},
        "validation_status": (
            "verified"
            if (
                investment_ready
                and bool(snapshot.get("performance_ready"))
                and bool(snapshot.get("backtest_ready"))
            )
            else "user_confirmed_local"
            if user_confirmed_ready
            else (
                "quarantined"
                if snapshot.get("lifecycle_status") == "quarantined"
                else (
                    "portfolio_ready"
                    if investment_ready
                    else "blocked"
                )
            )
        ),
        "backtests": backtests,
    }


def _strict_builds_have_official_provenance(
    db_path: Path,
    builds: list[SnapshotCycleBuild],
) -> bool:
    selected = [build for build in builds if build.selected]
    if not selected:
        return False
    with connect(db_path) as conn:
        for build in selected:
            for candidate in build.selected:
                facts = fetch_one(
                    conn,
                    """SELECT COUNT(DISTINCT f.year || '-' || f.quarter) AS n
                       FROM financial_period_facts f
                       JOIN financial_filing_revisions r
                         ON r.id = f.filing_revision_id
                       WHERE f.symbol = ?
                         AND r.verification_status = 'verified'
                         AND f.is_independent_quarter = 1
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
                                     AND resolution.verification_status =
                                         'verified'
                                     AND resolution.available_at <= ?
                               )
                         )""",
                    (
                        candidate.symbol,
                        build.signal_cutoff,
                        build.signal_cutoff,
                        build.signal_cutoff,
                    ),
                )
                if int((facts or {}).get("n") or 0) < build.quarter_count:
                    return False
                shares = fetch_one(
                    conn,
                    """SELECT 1 AS ok
                       FROM shares_outstanding_history
                       WHERE symbol = ?
                         AND verification_status = 'verified'
                         AND effective_from <= ?
                         AND (effective_to IS NULL OR effective_to >= ?)
                       LIMIT 1""",
                    (
                        candidate.symbol,
                        build.signal_price_date,
                        build.signal_price_date,
                    ),
                )
                if not shares:
                    return False
    return True


def _corporate_action_coverage_complete(
    db_path: Path,
    builds: list[SnapshotCycleBuild],
) -> bool:
    latest = _latest_price_date(db_path)
    with connect(db_path) as conn:
        for build in builds:
            for candidate in build.selected:
                end_date = min(
                    latest,
                    f"{build.hold_year + 1}-"
                    f"{build.execution_date[5:7]}-01",
                )
                row = fetch_one(
                    conn,
                    """SELECT 1 AS ok
                       FROM corporate_action_coverage
                       WHERE symbol = ?
                         AND coverage_status = 'verified'
                         AND start_date <= ?
                         AND end_date >= ?
                       LIMIT 1""",
                    (candidate.symbol, build.execution_date, end_date),
                )
                if not row:
                    return False
                unsupported = fetch_one(
                    conn,
                    """SELECT 1 AS blocked
                       FROM corporate_actions
                       WHERE symbol = ?
                         AND ex_date > ? AND ex_date <= ?
                         AND (
                           verification_status IN ('conflict', 'unsupported')
                           OR action_type IN ('rights_issue', 'other')
                           OR (
                             action_type = 'cash_dividend'
                             AND (
                               cash_vnd_per_share IS NULL
                               OR cash_vnd_per_share < 0
                               OR payment_date IS NULL
                             )
                           )
                           OR (
                             action_type IN ('stock_dividend', 'split')
                             AND (
                               share_factor IS NULL OR share_factor <= 0
                             )
                           )
                         )
                       LIMIT 1""",
                    (candidate.symbol, build.execution_date, end_date),
                )
                if unsupported:
                    return False
    return bool(builds)


def _strict_backtests_cover_ten_cycles(
    backtests: list[dict[str, Any]],
) -> bool:
    if not backtests:
        return False
    return all(
        int(result.get("cycle_count") or 0) >= 10
        and not result.get("excluded_cycles")
        for result in backtests
    )


def _benchmark_total_return_coverage_complete(
    config: AppConfig,
    builds: list[SnapshotCycleBuild],
) -> bool:
    """Require one verified benchmark pair for every completed strict cycle."""
    latest_price_date = _latest_price_date(config.db_path)
    checked = 0
    for build in builds:
        if build.excluded_reason or not build.selected:
            return False
        sell_date = (
            f"{build.hold_year + 1}-"
            f"{config.strategy.rebalance_month:02d}-01"
        )
        if sell_date > latest_price_date:
            continue
        pair = verified_benchmark_total_return_pair(
            config.db_path,
            config.strategy.benchmark_symbol,
            build.execution_date,
            sell_date,
            max_end_gap_days=config.strategy.max_rebalance_gap_days,
        )
        if pair is None:
            return False
        checked += 1
    return checked > 0


def _snapshot_market_inputs(
    config: AppConfig,
    selected: list[PE20QCandidate],
    execution_date: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, float]]:
    if not selected:
        return {}, {}
    symbols = [candidate.symbol for candidate in selected]
    prices = opens_on_date(
        config.db_path,
        symbols,
        execution_date,
        config.strategy.close_scale_vnd,
    )
    missing_prices = [symbol for symbol in symbols if symbol not in prices]
    if missing_prices:
        raise StrategySnapshotError(
            f"Missing execution opening prices for {execution_date}: "
            + ", ".join(missing_prices)
        )
    with sqlite3.connect(str(config.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        adv = query_adv_20d_historical(conn, symbols, execution_date)
    missing_adv = [symbol for symbol in symbols if adv.get(symbol, 0) <= 0]
    if missing_adv:
        raise StrategySnapshotError(
            f"Missing historical ADV before {execution_date}: "
            + ", ".join(missing_adv)
        )
    return prices, {symbol: float(adv[symbol]) for symbol in symbols}


def _performance_histories(
    config: AppConfig,
    builds: list[SnapshotCycleBuild],
    client: VCIClient | None,
    *,
    require_adjusted_prices: bool,
) -> tuple[dict[str, dict[str, float]], str]:
    latest = _latest_price_date(config.db_path)
    starts = [build.execution_date for build in builds]
    if not starts:
        return {}, ADJUSTED_BASIS
    start_date = min(starts)
    symbols = sorted(
        {
            item.symbol
            for build in builds
            for item in build.selected
        }
        | {config.strategy.benchmark_symbol}
    )
    histories = _load_cached_adjusted_histories(
        config.db_path, symbols, start_date, latest
    )
    missing = [
        symbol
        for symbol in symbols
        if not histories.get(symbol)
        or min(histories[symbol]) > start_date
        or max(histories[symbol]) < latest
    ]
    if missing and client is not None:
        fetched = _fetch_adjusted_histories(
            config, missing, start_date, latest, client
        )
        _store_adjusted_histories(config.db_path, fetched, latest)
        histories.update(fetched)
        missing = [symbol for symbol in symbols if not histories.get(symbol)]
    if missing:
        if require_adjusted_prices:
            raise StrategySnapshotError(
                "Missing adjusted total-return histories: "
                + ", ".join(missing[:30])
            )
        return {}, UNVERIFIED_PRICE_BASIS
    return histories, ADJUSTED_BASIS


def _fetch_adjusted_histories(
    config: AppConfig,
    symbols: list[str],
    start_date: str,
    end_date: str,
    client: VCIClient,
) -> dict[str, dict[str, float]]:
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    count_back = min(
        3_500, max(260, (end - start).days * 5 // 7 + 80)
    )
    histories: dict[str, dict[str, float]] = {}
    errors: list[str] = []

    def fetch(symbol: str) -> tuple[str, dict[str, float]]:
        bars = client.get_ohlcv(symbol, count_back=count_back)
        values = {
            date: float(bar["close"])
            for bar in bars
            if (date := _ts_to_date(bar.get("time")))
            and start_date <= date <= end_date
            and bar.get("close") is not None
            and float(bar["close"]) > 0
        }
        if not values:
            raise StrategySnapshotError("no adjusted bars returned")
        return symbol, values

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fetch, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                key, values = future.result()
                histories[key] = values
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")
    if errors:
        raise StrategySnapshotError(
            "Adjusted-price download incomplete: " + "; ".join(errors[:20])
        )
    return histories


def _load_cached_adjusted_histories(
    db_path: Path,
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> dict[str, dict[str, float]]:
    if not symbols:
        return {}
    with connect(db_path) as conn:
        rows = fetch_all(
            conn,
            f"""WITH latest AS (
                    SELECT symbol, price_date, MAX(source_as_of) AS source_as_of
                    FROM adjusted_price_history
                    WHERE symbol IN ({','.join('?' for _ in symbols)})
                      AND price_date BETWEEN ? AND ?
                    GROUP BY symbol, price_date
                )
                SELECT p.symbol, p.price_date, p.close_vnd
                FROM adjusted_price_history p
                JOIN latest l
                  ON l.symbol = p.symbol
                 AND l.price_date = p.price_date
                 AND l.source_as_of = p.source_as_of""",
            (*symbols, start_date, end_date),
        )
    result: dict[str, dict[str, float]] = {}
    for row in rows:
        result.setdefault(row["symbol"], {})[row["price_date"]] = float(
            row["close_vnd"]
        )
    return result


def _store_adjusted_histories(
    db_path: Path,
    histories: dict[str, dict[str, float]],
    source_as_of: str,
) -> None:
    rows = [
        (
            symbol,
            price_date,
            close,
            "VCI_GAP_CHART",
            "adjusted_total_return",
            source_as_of,
        )
        for symbol, values in histories.items()
        for price_date, close in values.items()
    ]
    if not rows:
        return
    with connect_rw(db_path) as conn:
        conn.executemany(
            """INSERT INTO adjusted_price_history
               (symbol, price_date, close_vnd, source, price_basis,
                source_as_of)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, price_date, source_as_of) DO UPDATE SET
                 close_vnd = excluded.close_vnd,
                 source = excluded.source,
                 price_basis = excluded.price_basis,
                 fetched_at = CURRENT_TIMESTAMP""",
            rows,
        )


def _run_snapshot_backtests(
    config: AppConfig,
    builds: list[SnapshotCycleBuild],
    capital_vnd: float,
    adjusted_histories: dict[str, dict[str, float]],
    price_basis: str,
) -> list[dict[str, Any]]:
    latest_price_date = _latest_price_date(config.db_path)
    grouped: dict[tuple[str, float, str], list[SnapshotCycleBuild]] = {}
    for build in builds:
        grouped.setdefault(
            (build.strategy, build.select_pct, build.pit_tier), []
        ).append(build)

    results: list[dict[str, Any]] = []
    for (strategy, pct, pit_tier), cycles in sorted(grouped.items()):
        yearly = []
        excluded = []
        for cycle in sorted(cycles, key=lambda item: item.hold_year):
            sell_date = (
                f"{cycle.hold_year + 1}-"
                f"{config.strategy.rebalance_month:02d}-01"
            )
            if sell_date > latest_price_date:
                continue
            if cycle.excluded_reason or not cycle.selected:
                excluded.append(
                    {
                        "hold_year": cycle.hold_year,
                        "reason": cycle.excluded_reason or "empty cycle",
                    }
                )
                continue
            yearly.append(
                _backtest_cycle(
                    config,
                    cycle,
                    capital_vnd,
                    sell_date,
                    adjusted_histories,
                    price_basis,
                )
            )
        if not yearly:
            continue
        compounded = math.prod(1.0 + row["return"] for row in yearly)
        benchmark_compounded = math.prod(
            1.0 + row["benchmark_return"] for row in yearly
        )
        net_cagr = compounded ** (1.0 / len(yearly)) - 1.0
        benchmark_cagr = (
            benchmark_compounded ** (1.0 / len(yearly)) - 1.0
        )
        results.append(
            {
                "strategy": strategy,
                "select_pct": pct,
                "pit_tier": pit_tier,
                "start_hold_year": yearly[0]["hold_year"],
                "end_hold_year": yearly[-1]["hold_year"],
                "cycle_count": len(yearly),
                "capital_vnd": capital_vnd,
                "net_cagr": net_cagr,
                "win_rate": (
                    sum(row["return"] > 0 for row in yearly) / len(yearly)
                ),
                "price_basis": price_basis,
                "benchmark_symbol": config.strategy.benchmark_symbol,
                "benchmark_cagr": benchmark_cagr,
                "yearly": yearly,
                "excluded_cycles": excluded,
            }
        )
    return results


def _backtest_cycle(
    config: AppConfig,
    cycle: SnapshotCycleBuild,
    capital_vnd: float,
    sell_date: str,
    adjusted_histories: dict[str, dict[str, float]],
    price_basis: str,
) -> dict[str, Any]:
    symbols = [candidate.symbol for candidate in cycle.selected]
    benchmark_pair: dict[str, Any] | None = None
    ledger = None
    if price_basis == VERIFIED_LEDGER_BASIS:
        benchmark_pair = verified_benchmark_total_return_pair(
            config.db_path,
            config.strategy.benchmark_symbol,
            cycle.execution_date,
            sell_date,
            max_end_gap_days=config.strategy.max_rebalance_gap_days,
        )
        if benchmark_pair is None:
            raise StrategySnapshotError(
                "Missing verified benchmark total-return values for "
                f"cycle {cycle.hold_year}"
            )
        sources = {
            str(cycle.rebalance_prices[symbol].get("source") or "")
            for symbol in symbols
        }
        if len(sources) != 1 or not next(iter(sources)):
            raise StrategySnapshotError(
                f"Mixed execution-price sources in cycle {cycle.hold_year}"
            )
        raw_sell = prices_on_date(
            config.db_path,
            symbols,
            benchmark_pair["end_date"],
            config.strategy.close_scale_vnd,
            require_verified=True,
            expected_source=next(iter(sources)),
        )
        missing = [symbol for symbol in symbols if symbol not in raw_sell]
        if missing:
            raise StrategySnapshotError(
                "Missing verified end-date prices for "
                f"{benchmark_pair['end_date']}: "
                + ", ".join(missing)
            )
        try:
            ledger = build_verified_ledger(
                config.db_path,
                symbols,
                cycle.execution_date,
                benchmark_pair["end_date"],
            )
        except CorporateActionError as exc:
            raise StrategySnapshotError(str(exc)) from exc
    else:
        raw_sell = first_prices_on_or_after(
            config.db_path,
            symbols,
            sell_date,
            config.strategy.max_rebalance_gap_days,
            config.strategy.close_scale_vnd,
        )
    if price_basis == UNVERIFIED_PRICE_BASIS:
        missing = [symbol for symbol in symbols if symbol not in raw_sell]
        if missing:
            raise StrategySnapshotError(
                f"Missing sell prices for {sell_date}: " + ", ".join(missing)
            )

    per_stock = capital_vnd / len(symbols)
    lot_size = config.strategy.lot_size
    buy_fee = config.strategy.broker_fee_bps / 10_000.0
    sell_fee_tax = (
        config.strategy.broker_fee_bps + config.strategy.sell_tax_bps
    ) / 10_000.0
    buy_cost = sell_value = 0.0
    position_count = 0
    for symbol in symbols:
        buy_price = float(cycle.rebalance_prices[symbol]["price_vnd"])
        desired = (
            math.floor(
                per_stock / (buy_price * (1.0 + buy_fee)) / lot_size
            )
            * lot_size
        )
        capacity = (
            math.floor(
                cycle.adv_shares[symbol]
                * config.strategy.participation_rate
                * config.strategy.accum_days
                / lot_size
            )
            * lot_size
        )
        shares = min(desired, capacity)
        if shares <= 0:
            continue
        if price_basis == VERIFIED_LEDGER_BASIS:
            action = ledger[symbol]
            sell_value += (
                shares
                * action.share_factor
                * float(raw_sell[symbol]["price_vnd"])
                * (1.0 - sell_fee_tax)
                + shares * action.cash_vnd_per_initial_share
            )
        elif price_basis == ADJUSTED_BASIS:
            buy_adj = _first_price(
                adjusted_histories[symbol],
                cycle.execution_date,
                config.strategy.max_rebalance_gap_days,
            )
            sell_adj = _first_price(
                adjusted_histories[symbol],
                sell_date,
                config.strategy.max_rebalance_gap_days,
            )
            if buy_adj is None or sell_adj is None:
                raise StrategySnapshotError(
                    f"Missing adjusted performance price for {symbol} "
                    f"in cycle {cycle.hold_year}"
                )
            gross_factor = sell_adj[1] / buy_adj[1]
        else:
            gross_factor = (
                float(raw_sell[symbol]["price_vnd"]) / buy_price
            )
        position_count += 1
        buy_market_value = shares * buy_price
        buy_cost += buy_market_value * (1.0 + buy_fee)
        if price_basis != VERIFIED_LEDGER_BASIS:
            sell_value += (
                buy_market_value * gross_factor * (1.0 - sell_fee_tax)
            )

    benchmark_symbol = config.strategy.benchmark_symbol
    if price_basis == VERIFIED_LEDGER_BASIS:
        benchmark_return = (
            float(benchmark_pair["end_value"])
            / float(benchmark_pair["start_value"])
            - 1.0
        )
    elif price_basis == ADJUSTED_BASIS:
        benchmark_buy = _first_price(
            adjusted_histories[benchmark_symbol],
            cycle.execution_date,
            config.strategy.max_rebalance_gap_days,
        )
        benchmark_sell = _first_price(
            adjusted_histories[benchmark_symbol],
            sell_date,
            config.strategy.max_rebalance_gap_days,
        )
        if benchmark_buy is None or benchmark_sell is None:
            raise StrategySnapshotError(
                f"Missing adjusted {benchmark_symbol} prices for "
                f"cycle {cycle.hold_year}"
            )
        benchmark_return = benchmark_sell[1] / benchmark_buy[1] - 1.0
    else:
        benchmark = first_prices_on_or_after(
            config.db_path,
            [benchmark_symbol],
            cycle.execution_date,
            config.strategy.max_rebalance_gap_days,
            1.0,
        ).get(benchmark_symbol)
        benchmark_end = first_prices_on_or_after(
            config.db_path,
            [benchmark_symbol],
            sell_date,
            config.strategy.max_rebalance_gap_days,
            1.0,
        ).get(benchmark_symbol)
        if benchmark and benchmark_end:
            benchmark_return = (
                float(benchmark_end["price_vnd"])
                / float(benchmark["price_vnd"])
                - 1.0
            )
        else:
            # Test fixtures and old databases may not have an index.  This
            # keeps the result explicitly unverified rather than fabricating
            # an authoritative comparison.
            benchmark_return = 0.0

    cash = max(0.0, capital_vnd - buy_cost)
    ending_value = cash + sell_value
    return {
        "hold_year": cycle.hold_year,
        "quarter_count": cycle.quarter_count,
        "return": ending_value / capital_vnd - 1.0,
        "benchmark_return": benchmark_return,
        "excess_return": ending_value / capital_vnd - 1.0 - benchmark_return,
        "selected_count": len(symbols),
        "position_count": position_count,
        "cash_drag_pct": cash / capital_vnd * 100.0,
        "valuation_date": (
            benchmark_pair["end_date"] if benchmark_pair else sell_date
        ),
        "data_checksum": cycle.data_checksum,
    }


def _first_price(
    history: dict[str, float],
    target_date: str,
    max_gap_days: int,
) -> tuple[str, float] | None:
    start = dt.date.fromisoformat(target_date)
    for offset in range(max_gap_days + 1):
        price_date = (start + dt.timedelta(days=offset)).isoformat()
        value = history.get(price_date)
        if value is not None and value > 0:
            return price_date, value
    return None


def _has_verified_price_observation(
    conn: sqlite3.Connection,
    symbol: str,
    price_date: str,
    metadata: dict[str, Any] | None,
) -> bool:
    if not metadata:
        return False
    digest = str(metadata.get("source_payload_sha256") or "")
    source = str(metadata.get("source") or "")
    if len(digest) != 64 or not source:
        return False
    verified = fetch_one(
        conn,
        """SELECT 1 AS ok
           FROM price_source_observations
           WHERE symbol = ? AND price_date = ? AND source = ?
             AND payload_sha256 = ?
             AND is_session_final = 1
             AND verification_status = 'verified'
           LIMIT 1""",
        (symbol, price_date, source, digest),
    )
    conflict = fetch_one(
        conn,
        """SELECT 1 AS blocked
           FROM price_source_observations
           WHERE symbol = ? AND price_date = ?
             AND verification_status = 'conflict'
           LIMIT 1""",
        (symbol, price_date),
    )
    return bool(verified) and not bool(conflict)


def _execution_price_provenance(
    db_path: Path,
    builds: list[SnapshotCycleBuild],
) -> str:
    keys = {
        (
            item.symbol,
            build.rebalance_prices[item.symbol]["price_date"],
        )
        for build in builds
        for item in build.selected
        if item.symbol in build.rebalance_prices
    }
    if not keys:
        return "legacy_unknown"
    with connect(db_path) as conn:
        table = fetch_one(
            conn,
            """SELECT 1 AS present FROM sqlite_master
               WHERE type = 'table' AND name = 'market_price_metadata'""",
        )
        if not table:
            return "legacy_unknown"
        verified = 0
        sources: set[str] = set()
        for symbol, price_date in keys:
            row = fetch_one(
                conn,
                """SELECT source, price_basis, raw_unit, is_provisional,
                          source_url, source_payload_sha256
                   FROM market_price_metadata
                   WHERE symbol = ? AND price_date = ?""",
                (symbol, price_date),
            )
            if (
                row
                and row["price_basis"] == "execution_unadjusted"
                and row["raw_unit"] == "THOUSAND_VND"
                and not bool(row["is_provisional"])
                and row["source"] != "LEGACY_UNKNOWN"
                and bool(str(row.get("source_url") or "").strip())
                and len(str(row.get("source_payload_sha256") or "")) == 64
                and _has_verified_price_observation(
                    conn, symbol, price_date, row
                )
            ):
                verified += 1
                sources.add(str(row["source"]))
    return (
        "verified_execution_unadjusted"
        if verified == len(keys) and len(sources) == 1
        else "legacy_unknown"
    )


def _signal_price_provenance(
    db_path: Path,
    builds: list[SnapshotCycleBuild],
) -> str:
    keys = {
        (item.symbol, build.signal_price_date)
        for build in builds
        for item in build.selected
    }
    if not keys:
        return "legacy_unknown"
    with connect(db_path) as conn:
        table = fetch_one(
            conn,
            """SELECT 1 AS present FROM sqlite_master
               WHERE type = 'table' AND name = 'market_price_metadata'""",
        )
        if not table:
            return "legacy_unknown"
        verified = 0
        sources: set[str] = set()
        for symbol, price_date in keys:
            row = fetch_one(
                conn,
                """SELECT source, price_basis, raw_unit, is_provisional,
                          source_url, source_payload_sha256
                   FROM market_price_metadata
                   WHERE symbol = ? AND price_date = ?""",
                (symbol, price_date),
            )
            if (
                row
                and row["price_basis"] == "execution_unadjusted"
                and row["raw_unit"] == "THOUSAND_VND"
                and not bool(row["is_provisional"])
                and row["source"] != "LEGACY_UNKNOWN"
                and bool(str(row.get("source_url") or "").strip())
                and len(str(row.get("source_payload_sha256") or "")) == 64
                and _has_verified_price_observation(
                    conn, symbol, price_date, row
                )
            ):
                verified += 1
                sources.add(str(row["source"]))
    return (
        "verified_signal_unadjusted"
        if verified == len(keys) and len(sources) == 1
        else "legacy_unknown"
    )


def _price_sources_consistent(
    db_path: Path,
    builds: list[SnapshotCycleBuild],
) -> bool:
    keys = {
        (item.symbol, price_date)
        for build in builds
        for item in build.selected
        for price_date in (
            build.signal_price_date,
            build.execution_date,
        )
    }
    if not keys:
        return False
    with connect(db_path) as conn:
        sources = set()
        for symbol, price_date in keys:
            row = fetch_one(
                conn,
                """SELECT source, source_payload_sha256
                   FROM market_price_metadata
                   WHERE symbol = ? AND price_date = ?""",
                (symbol, price_date),
            )
            if (
                not row
                or row["source"] == "LEGACY_UNKNOWN"
                or not _has_verified_price_observation(
                    conn, symbol, price_date, row
                )
            ):
                return False
            sources.add(str(row["source"]))
    return len(sources) == 1


def _persist_strict_cycles(
    conn: sqlite3.Connection,
    set_id: int,
    builds: list[SnapshotCycleBuild],
    price_basis: str,
) -> None:
    for build in builds:
        cycle_cur = conn.execute(
            """INSERT INTO strategy_cycle_snapshots
               (snapshot_set_id, strategy, select_pct, formation_year,
                hold_year, rebalance_date, universe_count, selected_count,
                quarter_count, pit_tier, price_basis, data_checksum,
                excluded_reason, signal_cutoff, signal_price_date,
                execution_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                set_id,
                build.strategy,
                build.select_pct,
                build.formation_year,
                build.hold_year,
                build.rebalance_date,
                build.universe_count,
                len(build.selected),
                build.quarter_count,
                build.pit_tier,
                price_basis,
                build.data_checksum,
                build.excluded_reason,
                build.signal_cutoff,
                build.signal_price_date,
                build.execution_date,
            ),
        )
        cycle_id = int(cycle_cur.lastrowid)
        _persist_cycle_items(
            conn,
            "strategy_cycle_snapshot_items",
            "cycle_snapshot_id",
            cycle_id,
            build,
        )


def _persist_research_cycles(
    conn: sqlite3.Connection,
    set_id: int,
    builds: list[SnapshotCycleBuild],
    price_basis: str,
) -> None:
    for build in builds:
        cycle_cur = conn.execute(
            """INSERT INTO strategy_research_cycles
               (snapshot_set_id, strategy, select_pct, formation_year,
                hold_year, rebalance_date, quarter_count, universe_count,
                selected_count, pit_tier, price_basis, data_checksum,
                excluded_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                set_id,
                build.strategy,
                build.select_pct,
                build.formation_year,
                build.hold_year,
                build.rebalance_date,
                build.quarter_count,
                build.universe_count,
                len(build.selected),
                build.pit_tier,
                price_basis,
                build.data_checksum,
                build.excluded_reason,
            ),
        )
        cycle_id = int(cycle_cur.lastrowid)
        if build.selected:
            conn.executemany(
                """INSERT INTO strategy_research_cycle_items
                   (research_cycle_id, symbol, signal_rank, avg_eps, pe,
                    market_cap_vnd, quarters_count, rebalance_price_vnd,
                    rebalance_price_date, adv_20d_shares, initial_weight)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _cycle_item_rows(cycle_id, build),
            )


def _persist_cycle_items(
    conn: sqlite3.Connection,
    table: str,
    id_column: str,
    cycle_id: int,
    build: SnapshotCycleBuild,
) -> None:
    if not build.selected:
        return
    conn.executemany(
        f"""INSERT INTO {table}
            ({id_column}, symbol, signal_rank, avg_eps_20q, pe_ttm_20q,
             market_cap_vnd, quarters_count, rebalance_price_vnd,
             rebalance_price_date, adv_20d_shares, initial_weight,
             signal_price_vnd, execution_price_vnd,
             execution_price_date, shares_outstanding,
             filing_revision_ids_json, price_provenance_json,
             provenance_checksum)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        _strict_cycle_item_rows(conn, cycle_id, build),
    )


def _strict_cycle_item_rows(
    conn: sqlite3.Connection,
    cycle_id: int,
    build: SnapshotCycleBuild,
) -> list[tuple[Any, ...]]:
    count = len(build.selected)
    rows: list[tuple[Any, ...]] = []
    for candidate in build.selected:
        if build.pit_tier == STRICT_PIT:
            provenance_payload = _candidate_official_provenance(
                conn,
                candidate.symbol,
                build.signal_cutoff,
                build.signal_price_date,
                build.quarter_count,
                build.execution_date,
            )
        else:
            provenance_payload = {
                "symbol": candidate.symbol,
                "trust_tier": build.pit_tier,
                "revisions": [],
                "shares": None,
                "signal_date": build.signal_price_date,
                "execution_date": build.execution_date,
                "price_provenance": {
                    "signal": {
                        "source": "USER_CONFIRMED_LOCAL_DATABASE",
                        "price_date": build.signal_price_date,
                    },
                    "execution": {
                        "source": "USER_CONFIRMED_LOCAL_DATABASE",
                        "price_date": build.execution_date,
                    },
                },
            }
        revisions = provenance_payload["revisions"]
        shares = provenance_payload["shares"]
        provenance_checksum = hashlib.sha256(
            json.dumps(
                provenance_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        execution = build.rebalance_prices[candidate.symbol]
        rows.append(
            (
                cycle_id,
                candidate.symbol,
                candidate.signal_rank,
                candidate.avg_eps_20q,
                candidate.pe_ttm_20q,
                candidate.market_cap_vnd,
                candidate.quarters_count,
                execution["price_vnd"],
                execution["price_date"],
                build.adv_shares[candidate.symbol],
                1.0 / count,
                candidate.buy_price_vnd,
                execution["price_vnd"],
                execution["price_date"],
                shares,
                json.dumps(revisions, separators=(",", ":")),
                json.dumps(
                    provenance_payload["price_provenance"],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                provenance_checksum,
            )
        )
    return rows


def _cycle_item_rows(
    cycle_id: int,
    build: SnapshotCycleBuild,
) -> list[tuple[Any, ...]]:
    count = len(build.selected)
    return [
        (
            cycle_id,
            candidate.symbol,
            candidate.signal_rank,
            candidate.avg_eps_20q,
            candidate.pe_ttm_20q,
            candidate.market_cap_vnd,
            candidate.quarters_count,
            build.rebalance_prices[candidate.symbol]["price_vnd"],
            build.rebalance_prices[candidate.symbol]["price_date"],
            build.adv_shares[candidate.symbol],
            1.0 / count,
        )
        for candidate in build.selected
    ]


def _persist_backtests(
    conn: sqlite3.Connection,
    set_id: int,
    backtests: list[dict[str, Any]],
) -> None:
    for result in backtests:
        conn.execute(
            """INSERT INTO strategy_backtest_results_v2
               (snapshot_set_id, strategy, select_pct, pit_tier,
                start_hold_year, end_hold_year, cycle_count, capital_vnd,
                net_cagr, win_rate, price_basis, benchmark_symbol,
                benchmark_cagr, yearly_json, excluded_cycles_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                set_id,
                result["strategy"],
                result["select_pct"],
                result["pit_tier"],
                result["start_hold_year"],
                result["end_hold_year"],
                result["cycle_count"],
                result["capital_vnd"],
                result["net_cagr"],
                result["win_rate"],
                result["price_basis"],
                result["benchmark_symbol"],
                result["benchmark_cagr"],
                json.dumps(
                    result["yearly"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                json.dumps(
                    result["excluded_cycles"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ),
        )


def _active_hold_year(
    rebalance_month: int, today: dt.date | None = None
) -> int:
    current = today or dt.date.today()
    hold_year = current.year
    if f"{hold_year}-{rebalance_month:02d}-01" > current.isoformat():
        hold_year -= 1
    return hold_year


def _latest_price_date(db_path: Path) -> str:
    with connect(db_path) as conn:
        row = fetch_one(
            conn,
            """SELECT MAX(time) AS latest
               FROM stock_price_history
               WHERE time GLOB '[0-9][0-9][0-9][0-9]-*'""",
        )
    latest = (row or {}).get("latest")
    if not latest:
        raise StrategySnapshotError("No market price date is available")
    return str(latest)
