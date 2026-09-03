"""Simple fund planner API: preferences, actual holdings, and trade deltas."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from ..config import get_config
from ..data.sync_service import (
    PriceRefreshError,
    SyncBusyError,
    refresh_portfolio_prices,
)
from ..fund.store import (
    delete_holdings,
    get_holdings,
    get_preferences,
    normalize_holdings,
    replace_holdings,
    save_preferences,
)
from ..fund.planner import (
    PlannerDataError,
    build_strategy_drift_targets,
    resolve_active_cycle,
)
from ..fund.snapshots import get_active_snapshot_status
from ..strategy.position_sizer import query_latest_price_date

router = APIRouter(prefix="/api/fund", tags=["fund"])
_cfg = get_config()


def _runtime_config():
    """Use reloaded production config while preserving test DB overrides."""
    current = get_config()
    try:
        if _cfg.db_path.resolve() != current.db_path.resolve():
            return _cfg
    except OSError:
        return _cfg
    return current


class HoldingInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    shares: int = Field(ge=0)


class HoldingsBody(BaseModel):
    holdings: list[HoldingInput]


class PreferenceBody(BaseModel):
    strategy: Literal["TTM_20Q", "LAST_8Q_PLUS"]
    select_pct: Literal[10.0, 12.0, 14.0, 16.0]


class PortfolioPlanBody(PreferenceBody):
    nav_vnd: float = Field(gt=0)
    holdings: list[HoldingInput] | None = None
    auto_sync: bool = True


@router.get("/preferences")
def read_preferences():
    return get_preferences(_runtime_config().db_path)


@router.get("/snapshot/status")
def read_snapshot_status():
    config = _runtime_config()
    snapshot = get_active_snapshot_status(config.db_path)
    research_ready = bool(
        config.allow_legacy_research_planner
        and snapshot
        and snapshot.get("research_planner_available")
    )
    user_confirmed_ready = bool(
        snapshot and snapshot.get("user_confirmed_ready")
    )
    return {
        "ready": bool(
            snapshot and snapshot.get("investment_ready")
        ) or user_confirmed_ready,
        "official_ready": bool(
            snapshot and snapshot.get("investment_ready")
        ),
        "planner_ready": bool(
            snapshot and snapshot.get("investment_ready")
        ) or user_confirmed_ready or research_ready,
        "trust_tier": (
            "strict_pit"
            if snapshot and snapshot.get("investment_ready")
            else "trusted_local"
            if user_confirmed_ready
            else "legacy_research"
            if research_ready
            else "blocked"
        ),
        "snapshot": snapshot,
    }


@router.put("/preferences")
def update_preferences(body: PreferenceBody):
    return save_preferences(
        _runtime_config().db_path, body.strategy, body.select_pct
    )


@router.get("/holdings")
def read_holdings():
    return get_holdings(_runtime_config().db_path)


@router.put("/holdings")
def update_holdings(body: HoldingsBody):
    config = _runtime_config()
    try:
        return replace_holdings(
            config.db_path, [item.model_dump() for item in body.holdings]
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/holdings", status_code=204)
def remove_holdings():
    delete_holdings(_runtime_config().db_path)
    return Response(status_code=204)


@router.post("/portfolio-plan")
def create_portfolio_plan(body: PortfolioPlanBody):
    """Scale the active buy-date strategy portfolio to current fund NAV."""
    config = _runtime_config()
    snapshot_status = get_active_snapshot_status(config.db_path)
    research_ready = bool(
        config.allow_legacy_research_planner
        and snapshot_status
        and snapshot_status.get("research_planner_available")
    )
    user_confirmed_ready = bool(
        snapshot_status and snapshot_status.get("user_confirmed_ready")
    )
    if not snapshot_status or not (
        snapshot_status.get("investment_ready")
        or user_confirmed_ready
        or research_ready
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SNAPSHOT_NOT_VERIFIED",
                "message": (
                    "Snapshot chiến lược chưa được xác minh đầy đủ; "
                    "hệ thống đã khóa lập danh mục để tránh quyết định sai."
                ),
                "blocking_issues": (
                    snapshot_status.get("blocking_issues", [])
                    if snapshot_status else ["NO_ACTIVE_SNAPSHOT"]
                ),
            },
        )
    try:
        if body.holdings is None:
            holdings = get_holdings(config.db_path)["holdings"]
            holdings_source = "saved"
        else:
            holdings = normalize_holdings(
                config.db_path, [item.model_dump() for item in body.holdings]
            )
            holdings_source = "request"
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        cycle = resolve_active_cycle(
            config,
            body.strategy,
            body.select_pct,
        )
    except PlannerDataError as exc:
        raise HTTPException(422, str(exc)) from exc

    sync_warnings: list[str] = []
    if body.auto_sync:
        try:
            sync_warnings = refresh_portfolio_prices(
                config,
                cycle.symbols
                + [item["symbol"] for item in holdings]
                + [config.strategy.benchmark_symbol],
            )
        except SyncBusyError as exc:
            raise HTTPException(
                409,
                "Dữ liệu đang được đồng bộ. Hãy chờ hoàn tất rồi tính lại.",
            ) from exc
        except PriceRefreshError as exc:
            raise HTTPException(503, str(exc)) from exc

    # Strict results use the verified corporate-action ledger. The explicit
    # legacy-research opt-in uses the stored vendor-adjusted series and is
    # labelled as such throughout the API, UI and CSV.
    adjusted_prices = None
    valuation_date = query_latest_price_date(config.db_path)

    try:
        target = build_strategy_drift_targets(
            config,
            body.nav_vnd,
            cycle,
            extra_symbols=[item["symbol"] for item in holdings],
            valuation_date=valuation_date,
            adjusted_prices=adjusted_prices,
        )
    except PlannerDataError as exc:
        raise HTTPException(422, str(exc)) from exc

    # A failed refresh/snapshot/plan must not change the user's default.
    save_preferences(config.db_path, body.strategy, body.select_pct)

    current_map = {item["symbol"]: item["shares"] for item in holdings}
    target_map = {item["symbol"]: item for item in target["positions"]}
    all_symbols = sorted(set(current_map) | set(target_map))
    prices = target["prices"]

    rows = []
    warnings: list[dict[str, str]] = [
        {"code": "SYNC_WARNING", "message": message}
        for message in sync_warnings
    ]
    cycle_trust_tier = getattr(cycle, "trust_tier", "strict_pit")
    if cycle_trust_tier == "trusted_local":
        warnings.extend([
            {
                "code": "USER_CONFIRMED_LOCAL_DATA",
                "message": (
                    "Danh mục dùng dữ liệu trong vietnam_stocks.db đã được "
                    "chủ quỹ xác nhận để sử dụng. Nhãn này không có nghĩa là "
                    "hệ thống đã đối chiếu lại từng tài liệu công bố chính thức."
                ),
            },
            {
                "code": "VENDOR_ADJUSTED_PERFORMANCE",
                "message": (
                    "Hiệu suất và tỷ trọng trôi dùng chuỗi giá điều chỉnh "
                    "đang lưu trong database; giá mua dùng giá mở cửa ngày "
                    "thực thi đã được khóa trong snapshot."
                ),
            },
        ])
    if cycle_trust_tier == "legacy_research":
        warnings.extend([
            {
                "code": "LEGACY_RESEARCH_DATA",
                "message": (
                    "Danh mục này dùng snapshot vendor hiện có. "
                    "Nó không phải strict PIT và chưa được xác minh "
                    "bằng tài liệu công bố chính thức."
                ),
            },
            {
                "code": "VENDOR_ADJUSTED_PERFORMANCE",
                "message": (
                    "Lãi/lỗ và tỷ trọng trôi dùng chuỗi giá điều "
                    "chỉnh Vietcap; giá mua dùng giá mở cửa trong "
                    "database ngày thực thi."
                ),
            },
            {
                "code": "STORED_PRICE_DATE",
                "message": (
                    "Chế độ nhanh đang dùng phiên giá đủ độ phủ "
                    f"gần nhất trong database: {target['price_date']}."
                ),
            },
        ])
        if not getattr(cycle, "strategy_parameters_match", True):
            warnings.append({
                "code": "LEGACY_CONFIG_HASH_MISMATCH",
                "message": (
                    "Cấu hình hiện tại khác fingerprint khi snapshot "
                    "được dựng. Danh sách/rank vẫn lấy nguyên bản "
                    "từ snapshot; lot size và giới hạn thanh khoản dùng "
                    "cấu hình hiện tại."
                ),
            })
        elif not getattr(cycle, "config_hash_matches", True):
            warnings.append({
                "code": "LEGACY_METHODOLOGY_VERSION_MISMATCH",
                "message": (
                    "Tham số chiến lược và sizing vẫn khớp snapshot, nhưng "
                    "phiên bản phương pháp PIT trong code đã thay đổi. "
                    "Danh sách/rank tiếp tục lấy nguyên bản từ snapshot cũ "
                    "và không được gắn nhãn strict PIT."
                ),
            })
    current_value = buy_value = sell_value = target_deployed = 0.0
    for symbol in all_symbols:
        target_item = target_map.get(symbol)
        price_item = prices.get(symbol)
        price = float(price_item["price_vnd"])
        current_shares = int(current_map.get(symbol, 0))
        target_shares = int(target_item["target_shares"]) if target_item else 0
        delta = target_shares - current_shares
        target_value = target_shares * price
        current_value += current_shares * price
        target_deployed += target_value
        if delta > 0:
            action = "MUA"
            buy_value += delta * price
        elif delta < 0:
            action = "BÁN"
            sell_value += abs(delta) * price
        else:
            action = "GIỮ"

        rows.append({
            "symbol": symbol,
            "signal_rank": target_item.get("signal_rank") if target_item else None,
            "source": target_item.get("source") if target_item else "CURRENT_ONLY",
            "rebalance_price_vnd": (
                target_item.get("rebalance_price_vnd") if target_item else None
            ),
            "rebalance_price_date": (
                target_item.get("rebalance_price_date") if target_item else None
            ),
            "adjusted_rebalance_price_vnd": (
                target_item.get("adjusted_rebalance_price_vnd")
                if target_item else None
            ),
            "current_price_vnd": price,
            "price_date": target["price_date"],
            "price_return_pct": (
                target_item.get("price_return_pct")
                if target_item
                else None
            ),
            "initial_weight_pct": (
                target_item.get("initial_weight_pct", 0.0) if target_item else 0.0
            ),
            "drift_weight_pct": (
                target_item.get("drift_weight_pct", 0.0) if target_item else 0.0
            ),
            "target_weight_pct": round(target_value / body.nav_vnd * 100, 4),
            "desired_shares": (
                target_item.get("desired_shares", 0) if target_item else 0
            ),
            "target_shares": target_shares,
            "target_value_vnd": target_value,
            "adv_shares": target_item.get("adv_shares") if target_item else None,
            "capacity_shares": (
                target_item.get("capacity_shares") if target_item else None
            ),
            "liquidity_limited": (
                target_item.get("liquidity_limited", False)
                if target_item
                else False
            ),
            "current_shares": current_shares,
            "delta_shares": delta,
            "action": action,
            "trade_value_vnd": abs(delta) * price,
        })

    implied_cash = body.nav_vnd - current_value
    if implied_cash < 0:
        warnings.append({
            "code": "NEGATIVE_IMPLIED_CASH",
            "message": (
                "Giá trị cổ phiếu hiện có lớn hơn NAV. "
                "Hãy kiểm tra lại NAV hoặc số lượng đã nhập."
            ),
        })

    rows.sort(key=lambda row: (
        row["signal_rank"] is None,
        row["signal_rank"] or 9999,
        row["symbol"],
    ))
    return {
        "nav_vnd": body.nav_vnd,
        "strategy": body.strategy,
        "select_pct": body.select_pct,
        "holdings_source": holdings_source,
        "has_current_holdings": bool(holdings),
        "formation_year": target["formation_year"],
        "hold_year": target["hold_year"],
        "rebalance_date": target["rebalance_date"],
        "signal_cutoff": target.get("signal_cutoff"),
        "signal_price_date": target.get("signal_price_date"),
        "execution_date": target.get("execution_date"),
        "snapshot_id": target.get("snapshot_id"),
        "snapshot_set_id": target.get("snapshot_set_id"),
        "snapshot_created_at": target.get("snapshot_created_at"),
        "financial_data_version_id": target.get("financial_data_version_id"),
        "financial_content_hash": target.get("financial_content_hash"),
        "methodology_version": target.get("methodology_version"),
        "universe_count": target.get("universe_count"),
        "price_date": target["price_date"],
        "price_basis": "strategy_date_drift",
        "performance_basis": target.get(
            "performance_basis", "unadjusted_price"
        ),
        "trust_tier": target.get("trust_tier", cycle_trust_tier),
        "performance_source_as_of": target.get(
            "performance_source_as_of"
        ),
        "model_growth_multiple": target["model_growth_multiple"],
        "benchmark": target.get("benchmark"),
        "summary": {
            "strategy_price_return_pct": target["summary"].get(
                "strategy_price_return_pct",
                round((target["model_growth_multiple"] - 1.0) * 100.0, 4),
            ),
            "strategy_total_return_pct": target["summary"].get(
                "strategy_price_return_pct",
                round((target["model_growth_multiple"] - 1.0) * 100.0, 4),
            ),
            "model_cash_weight_pct": target["summary"].get(
                "model_cash_weight_pct", 0.0
            ),
            "model_cash_vnd": target["summary"].get(
                "model_cash_vnd", 0.0
            ),
            "model_value_per_100m_vnd": target["summary"].get(
                "model_value_per_100m_vnd",
                round(100_000_000.0 * target["model_growth_multiple"]),
            ),
            "benchmark_symbol": target["summary"].get(
                "benchmark_symbol",
                config.strategy.benchmark_symbol,
            ),
            "benchmark_return_pct": target["summary"].get(
                "benchmark_return_pct"
            ),
            "benchmark_value_per_100m_vnd": target["summary"].get(
                "benchmark_value_per_100m_vnd"
            ),
            "excess_return_pct": target["summary"].get(
                "excess_return_pct"
            ),
            "gainers_count": target["summary"].get(
                "gainers_count",
                sum(
                    (item.get("price_return_pct") or 0) > 0
                    for item in target["positions"]
                ),
            ),
            "losers_count": target["summary"].get(
                "losers_count",
                sum(
                    (item.get("price_return_pct") or 0) < 0
                    for item in target["positions"]
                ),
            ),
            "unchanged_count": target["summary"].get(
                "unchanged_count",
                sum(
                    (item.get("price_return_pct") or 0) == 0
                    for item in target["positions"]
                ),
            ),
            "target_stock_count": sum(1 for row in rows if row["target_shares"] > 0),
            "target_deployed_vnd": target_deployed,
            "target_cash_vnd": body.nav_vnd - target_deployed,
            "liquidity_limited_count": target["summary"][
                "liquidity_limited_count"
            ],
            "current_holdings_value_vnd": current_value,
            "implied_cash_vnd": implied_cash,
            "estimated_buy_vnd": buy_value,
            "estimated_sell_vnd": sell_value,
        },
        "positions": rows,
        "warnings": warnings,
    }
