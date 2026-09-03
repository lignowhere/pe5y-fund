"""Resolve the immutable strategy cycle currently in force."""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass
from typing import Any

from ..config import AppConfig
from ..strategy.signal_pe_ttm_20q import PE20QCandidate
from ..strategy.variants import STRATEGY_PARAMS
from .snapshots import (
    load_active_cycle_snapshot,
    load_legacy_research_cycle_snapshot,
    load_trusted_local_cycle_snapshot,
    strategy_config_fingerprint,
)
from .trusted_local import TRUSTED_LOCAL
from .market_data import opens_on_date, strategy_timing


class PlannerDataError(ValueError):
    """Raised when a complete portfolio plan cannot be calculated."""


@dataclass(frozen=True)
class ActiveCycle:
    formation_year: int
    hold_year: int
    rebalance_date: str
    selected: list[PE20QCandidate]
    signal_cutoff: str | None = None
    signal_price_date: str | None = None
    execution_date: str | None = None
    snapshot_id: int | None = None
    snapshot_set_id: int | None = None
    financial_data_version_id: int | None = None
    financial_content_hash: str | None = None
    methodology_version: str | None = None
    snapshot_created_at: str | None = None
    universe_count: int | None = None
    rebalance_prices: dict[str, dict[str, Any]] | None = None
    adv_shares: dict[str, float] | None = None
    canonical_price_source: str | None = None
    trust_tier: str = "strict_pit"
    config_hash_matches: bool = True
    strategy_parameters_match: bool = True

    @property
    def symbols(self) -> list[str]:
        return [candidate.symbol for candidate in self.selected]


def resolve_active_cycle(
    config: AppConfig,
    strategy: str,
    select_pct: float,
    *,
    today: dt.date | None = None,
) -> ActiveCycle:
    if strategy not in STRATEGY_PARAMS:
        raise PlannerDataError(f"Chiến lược không hợp lệ: {strategy}")
    if select_pct not in {10.0, 12.0, 14.0, 16.0}:
        raise PlannerDataError(
            "Tỷ lệ chọn phải là 10%, 12%, 14% hoặc 16%"
        )

    current = today or dt.date.today()
    hold_year = current.year
    formation_year = hold_year - 1
    month = config.strategy.rebalance_month
    rebalance_date = f"{hold_year}-{month:02d}-01"
    if rebalance_date > current.isoformat():
        hold_year -= 1
        formation_year -= 1
        rebalance_date = f"{hold_year}-{month:02d}-01"

    snapshot = load_active_cycle_snapshot(
        config.db_path, strategy, select_pct, hold_year
    )
    trust_tier = "strict_pit"
    if not snapshot:
        snapshot = load_trusted_local_cycle_snapshot(
            config.db_path, strategy, select_pct, hold_year
        )
        trust_tier = TRUSTED_LOCAL
    if not snapshot and config.allow_legacy_research_planner:
        snapshot = load_legacy_research_cycle_snapshot(
            config.db_path, strategy, select_pct, hold_year
        )
        trust_tier = "legacy_research"
    if not snapshot:
        raise PlannerDataError(
            "Chưa có snapshot chiến lược đã được backtest cho chu kỳ hiện hành. "
            "Hãy hoàn tất đồng bộ tài chính và tạo lại snapshot."
        )

    _, current_config_hash = strategy_config_fingerprint(
        config,
        methodology_version=str(
            snapshot.get("methodology_version") or ""
        ),
        pit_policy=str(snapshot.get("pit_policy") or ""),
    )
    config_hash_matches = snapshot["config_hash"] == current_config_hash
    try:
        snapshot_strategy = json.loads(
            snapshot.get("config_json") or "{}"
        ).get("strategy")
    except (TypeError, json.JSONDecodeError):
        snapshot_strategy = None
    strategy_parameters_match = (
        snapshot_strategy == asdict(config.strategy)
    )
    if not config_hash_matches and trust_tier != "legacy_research":
        raise PlannerDataError(
            "Cấu hình chiến lược đã thay đổi sau lần backtest gần nhất. "
            "Hãy chạy lại backtest và tạo snapshot mới trước khi lập danh mục."
        )

    selected = [
        PE20QCandidate(
            symbol=item["symbol"],
            avg_eps_20q=float(item["avg_eps_20q"]),
            pe_ttm_20q=float(item["pe_ttm_20q"]),
            market_cap_vnd=float(item["market_cap_vnd"]),
            signal_rank=int(item["signal_rank"]),
            buy_price_vnd=float(item["rebalance_price_vnd"]),
            quarters_count=int(item["quarters_count"]),
        )
        for item in snapshot["items"]
    ]
    if not selected:
        raise PlannerDataError(
            "Snapshot chu kỳ hiện hành không có cổ phiếu được chọn"
        )

    signal_cutoff = snapshot.get("signal_cutoff")
    signal_price_date = snapshot.get("signal_price_date")
    execution_date = snapshot.get("execution_date")
    rebalance_prices = {
        item["symbol"]: {
            "price_vnd": float(
                item.get("execution_price_vnd")
                or item["rebalance_price_vnd"]
            ),
            "price_date": (
                item.get("execution_price_date")
                or item["rebalance_price_date"]
            ),
        }
        for item in snapshot["items"]
    }
    if trust_tier == "legacy_research":
        timing = strategy_timing(
            config.db_path,
            rebalance_date,
            config.strategy.benchmark_symbol,
        )
        signal_cutoff = timing["signal_cutoff"]
        signal_price_date = timing["signal_price_date"]
        execution_date = timing["execution_date"]
        rebalance_prices = opens_on_date(
            config.db_path,
            [item.symbol for item in selected],
            execution_date,
            config.strategy.close_scale_vnd,
        )
        missing_open = [
            item.symbol
            for item in selected
            if item.symbol not in rebalance_prices
        ]
        if missing_open:
            raise PlannerDataError(
                "Legacy research data is missing execution-date opens: "
                + ", ".join(missing_open)
            )

    return ActiveCycle(
        formation_year=formation_year,
        hold_year=hold_year,
        rebalance_date=rebalance_date,
        signal_cutoff=signal_cutoff,
        signal_price_date=signal_price_date,
        execution_date=execution_date,
        selected=selected,
        snapshot_id=int(snapshot["id"]),
        snapshot_set_id=int(snapshot["snapshot_set_id"]),
        financial_data_version_id=int(snapshot["financial_data_version_id"]),
        financial_content_hash=snapshot["financial_content_hash"],
        methodology_version=snapshot["methodology_version"],
        snapshot_created_at=snapshot["snapshot_set_created_at"],
        universe_count=int(snapshot["universe_count"]),
        rebalance_prices=rebalance_prices,
        adv_shares={
            item["symbol"]: float(item["adv_20d_shares"])
            for item in snapshot["items"]
        },
        canonical_price_source=(
            _canonical_price_source(snapshot["items"])
            if trust_tier == "strict_pit"
            else None
        ),
        trust_tier=trust_tier,
        config_hash_matches=config_hash_matches,
        strategy_parameters_match=strategy_parameters_match,
    )


def _canonical_price_source(items: list[dict[str, Any]]) -> str | None:
    sources: set[str] = set()
    for item in items:
        try:
            payload = json.loads(item.get("price_provenance_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            return None
        for label in ("signal", "execution"):
            source = (payload.get(label) or {}).get("source")
            if not source:
                return None
            sources.add(str(source))
    return next(iter(sources)) if len(sources) == 1 else None
