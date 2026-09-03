"use client";

import { useMemo, useState } from "react";
import { FundPlanPosition, FundPlanResult } from "@/lib/api";
import { fmtVND, fmtPrice } from "@/lib/format";

type SortKey =
  | "symbol"
  | "rebalance_price_vnd"
  | "current_price_vnd"
  | "price_return_pct"
  | "initial_weight_pct"
  | "drift_weight_pct"
  | "target_shares"
  | "current_shares"
  | "delta_shares"
  | "trade_value_vnd";

type SortDirection = "asc" | "desc";

const COMPACT_WARNING_CODES = new Set([
  "USER_CONFIRMED_LOCAL_DATA",
  "VENDOR_ADJUSTED_PERFORMANCE",
  "LEGACY_RESEARCH_DATA",
  "STORED_PRICE_DATE",
]);

function comparePositions(
  left: FundPlanPosition,
  right: FundPlanPosition,
  key: SortKey,
  direction: SortDirection,
) {
  const leftValue = left[key];
  const rightValue = right[key];

  // Missing historical prices always stay at the bottom of the table.
  if (leftValue == null && rightValue == null) {
    return left.symbol.localeCompare(right.symbol);
  }
  if (leftValue == null) return 1;
  if (rightValue == null) return -1;

  const comparison = typeof leftValue === "string"
    ? leftValue.localeCompare(String(rightValue), "vi", {
        sensitivity: "base",
        numeric: true,
      })
    : Number(leftValue) - Number(rightValue);

  if (comparison === 0) {
    return left.symbol.localeCompare(right.symbol);
  }
  return direction === "asc" ? comparison : -comparison;
}

function downloadCsv(data: FundPlanResult) {
  const metadata = [
    `# Strategy: ${data.strategy}`,
    `# NAV: ${Math.round(data.nav_vnd)}`,
    `# Select: ${data.select_pct}%`,
    `# Signal cutoff: ${data.signal_cutoff ?? ""}`,
    `# Execution date: ${data.execution_date ?? data.rebalance_date}`,
    `# Strategy snapshot: ${data.snapshot_id ?? ""}`,
    `# Financial data version: ${data.financial_data_version_id ?? ""}`,
    `# Financial data hash: ${data.financial_content_hash ?? ""}`,
    `# Current price date: ${data.price_date ?? "latest"}`,
    `# Performance basis: ${data.performance_basis}`,
    `# Trust tier: ${data.trust_tier}`,
    `# Performance source as of: ${data.performance_source_as_of ?? ""}`,
    `# Ledger total return: ${data.summary.strategy_total_return_pct}%`,
    `# Model cash weight: ${data.summary.model_cash_weight_pct}%`,
    `# ${data.summary.benchmark_symbol} price-index comparison (not authoritative): ${data.summary.benchmark_return_pct ?? ""}%`,
    `# Excess return vs ${data.summary.benchmark_symbol}: ${data.summary.excess_return_pct ?? ""}%`,
  ];
  const header = [
    "Symbol", "RebalancePriceDate", "RebalancePrice",
    "ShareFactor", "CashDividendPerInitialShare", "CurrentPrice",
    "PriceReturnPct", "InitialWeightPct", "StrategyDriftWeightPct", "ActualTargetWeightPct",
    "TargetShares", "CurrentShares", "DeltaShares", "Action", "TradeValue",
  ].join(",");
  const rows = data.positions.map((position) => [
    position.symbol,
    position.rebalance_price_date ?? "",
    position.rebalance_price_vnd ?? "",
    position.corporate_action_share_factor ?? "",
    position.cash_dividend_vnd_per_initial_share ?? "",
    position.current_price_vnd,
    position.price_return_pct ?? "",
    position.initial_weight_pct,
    position.drift_weight_pct,
    position.target_weight_pct,
    position.target_shares,
    position.current_shares,
    position.delta_shares,
    position.action,
    Math.round(position.trade_value_vnd),
  ].join(","));
  const blob = new Blob(
    ["\uFEFF" + [...metadata, header, ...rows].join("\n")],
    { type: "text/csv;charset=utf-8" },
  );
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `danh-muc-${data.strategy}-${data.price_date ?? "latest"}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function actionLabel(action: string) {
  return action;
}

function actionClass(action: string) {
  if (action === "MUA") return "text-emerald-600 dark:text-emerald-400";
  if (action === "BÁN") return "text-rose-600 dark:text-rose-400";
  return "text-slate-400";
}

export function PortfolioResults({ data }: { data: FundPlanResult }) {
  const summary = data.summary;
  const compactWarnings = data.warnings.filter((warning) =>
    COMPACT_WARNING_CODES.has(warning.code)
  );
  const actionWarnings = data.warnings.filter((warning) =>
    !COMPACT_WARNING_CODES.has(warning.code)
  );
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const sortedPositions = useMemo(() => {
    if (!sortKey) return data.positions;
    return [...data.positions].sort((left, right) =>
      comparePositions(left, right, sortKey, sortDirection)
    );
  }, [data.positions, sortDirection, sortKey]);

  function changeSort(nextKey: SortKey) {
    if (sortKey === nextKey) {
      setSortDirection((current) => current === "asc" ? "desc" : "asc");
      return;
    }
    setSortKey(nextKey);
    setSortDirection(nextKey === "symbol" ? "asc" : "desc");
  }

  return (
    <section className="space-y-4">
      {actionWarnings.map((warning, index) => (
        <div
          key={`${warning.code}-${index}`}
          className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300"
          role="alert"
        >
          {warning.message}
        </div>
      ))}
      {compactWarnings.length > 0 && (
        <details className="rounded-xl border border-amber-200 bg-amber-50/70 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/20 dark:text-amber-200">
          <summary className="cursor-pointer px-4 py-2 font-medium">
            Ghi chú về nguồn dữ liệu ({compactWarnings.length})
          </summary>
          <ul className="space-y-1 border-t border-amber-200 px-8 py-3 text-xs dark:border-amber-800">
            {compactWarnings.map((warning, index) => (
              <li key={`${warning.code}-${index}`} className="list-disc">
                {warning.message}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <SummaryCard label="NAV hiện tại" value={fmtVND(data.nav_vnd)} />
        <SummaryCard label="Vốn mục tiêu" value={fmtVND(summary.target_deployed_vnd)} />
        <SummaryCard label="Tiền mặt mục tiêu" value={fmtVND(summary.target_cash_vnd)} />
        <SummaryCard label="Số mã cần nắm giữ" value={String(summary.target_stock_count)} />
        {data.has_current_holdings && (
          <>
            <SummaryCard label="Giá trị CP hiện có" value={fmtVND(summary.current_holdings_value_vnd)} />
            <SummaryCard label="Tiền mặt suy ra" value={fmtVND(summary.implied_cash_vnd)} />
            <SummaryCard label="Ước tính cần mua" value={fmtVND(summary.estimated_buy_vnd)} tone="buy" />
            <SummaryCard label="Ước tính cần bán" value={fmtVND(summary.estimated_sell_vnd)} tone="sell" />
          </>
        )}
      </div>

      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-900">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-5 py-4 dark:border-slate-700">
          <div>
            <h2 className="text-lg font-bold">Danh mục mục tiêu</h2>
            <p className="text-xs text-slate-500">
              Tín hiệu từ phiên {data.signal_price_date ?? "—"} · mua mở cửa{" "}
              {data.execution_date ?? data.rebalance_date} · giữ đến{" "}
              {data.price_date ?? "gần nhất"}
            </p>
            {data.snapshot_id && (
              <p className="mt-1 text-[11px] text-slate-400">
                Snapshot #{data.snapshot_id} · dữ liệu tài chính v{data.financial_data_version_id}
                {data.financial_content_hash
                  ? ` · ${data.financial_content_hash.slice(0, 10)}`
                  : ""}
                {data.methodology_version === "official_revision_pit_v2"
                  ? " · strict PIT theo revision tài liệu gốc"
                  : ""}
                {data.trust_tier === "legacy_research"
                  ? " · legacy_research (vendor, chưa xác minh chính thức)"
                  : ""}
                {data.trust_tier === "trusted_local"
                  ? " · dữ liệu cục bộ đã được chủ quỹ xác nhận"
                  : ""}
                {data.universe_count
                  ? ` · tập sàng lọc ${data.universe_count} mã`
                  : ""}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={() => downloadCsv(data)}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
          >
            Xuất CSV
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1200px] text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500 dark:bg-slate-800">
              <tr>
                <SortableHeader
                  label="Mã"
                  sortKey="symbol"
                  activeKey={sortKey}
                  direction={sortDirection}
                  onSort={changeSort}
                />
                <SortableHeader
                  label="Giá ngày chiến lược"
                  sortKey="rebalance_price_vnd"
                  activeKey={sortKey}
                  direction={sortDirection}
                  onSort={changeSort}
                  align="right"
                />
                <SortableHeader
                  label="Giá hiện tại"
                  sortKey="current_price_vnd"
                  activeKey={sortKey}
                  direction={sortDirection}
                  onSort={changeSort}
                  align="right"
                />
                <SortableHeader
                  label="Lãi/lỗ"
                  sortKey="price_return_pct"
                  activeKey={sortKey}
                  direction={sortDirection}
                  onSort={changeSort}
                  align="right"
                />
                <SortableHeader
                  label="Tỷ trọng ban đầu"
                  sortKey="initial_weight_pct"
                  activeKey={sortKey}
                  direction={sortDirection}
                  onSort={changeSort}
                  align="right"
                />
                <SortableHeader
                  label="Tỷ trọng hiện tại theo chiến lược"
                  sortKey="drift_weight_pct"
                  activeKey={sortKey}
                  direction={sortDirection}
                  onSort={changeSort}
                  align="right"
                />
                <SortableHeader
                  label="SL mục tiêu"
                  sortKey="target_shares"
                  activeKey={sortKey}
                  direction={sortDirection}
                  onSort={changeSort}
                  align="right"
                />
                {data.has_current_holdings && (
                  <>
                    <SortableHeader
                      label="SL hiện có"
                      sortKey="current_shares"
                      activeKey={sortKey}
                      direction={sortDirection}
                      onSort={changeSort}
                      align="right"
                    />
                    <SortableHeader
                      label="Cần mua/bán"
                      sortKey="delta_shares"
                      activeKey={sortKey}
                      direction={sortDirection}
                      onSort={changeSort}
                      align="right"
                    />
                    <SortableHeader
                      label="Giá trị"
                      sortKey="trade_value_vnd"
                      activeKey={sortKey}
                      direction={sortDirection}
                      onSort={changeSort}
                      align="right"
                    />
                  </>
                )}
              </tr>
            </thead>
            <tbody>
              {sortedPositions.map((position, index) => (
                <tr
                  key={position.symbol}
                  className={`border-t border-slate-100 dark:border-slate-800 ${
                    index % 2 ? "bg-slate-50/60 dark:bg-slate-800/30" : ""
                  }`}
                >
                  <td className="px-4 py-3 font-bold">{position.symbol}</td>
                  <td className="px-4 py-3 text-right">
                    <div className="font-mono">
                      {position.rebalance_price_vnd
                        ? fmtPrice(position.rebalance_price_vnd)
                        : "—"}
                    </div>
                    <div className="text-[11px] text-slate-400">
                      {position.rebalance_price_date ?? ""}
                    </div>
                    {(position.corporate_action_count ?? 0) > 0 && (
                      <div className="text-[11px] text-blue-500">
                        Hệ số CP:{" "}
                        {(position.corporate_action_share_factor ?? 1).toFixed(4)}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right font-mono">{fmtPrice(position.current_price_vnd)}</td>
                  <td className={`px-4 py-3 text-right font-mono font-semibold ${
                    (position.price_return_pct ?? 0) > 0
                      ? "text-emerald-600 dark:text-emerald-400"
                      : (position.price_return_pct ?? 0) < 0
                        ? "text-rose-600 dark:text-rose-400"
                        : "text-slate-500"
                  }`}>
                    {position.price_return_pct == null
                      ? "—"
                      : `${position.price_return_pct > 0 ? "+" : ""}${position.price_return_pct.toFixed(2)}%`}
                  </td>
                  <td className="px-4 py-3 text-right font-mono">{position.initial_weight_pct.toFixed(2)}%</td>
                  <td className="px-4 py-3 text-right font-mono font-semibold text-blue-600 dark:text-blue-400">
                    {position.drift_weight_pct.toFixed(2)}%
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="font-mono font-semibold">{position.target_shares.toLocaleString("vi-VN")}</div>
                    {position.liquidity_limited && (
                      <div className="text-[11px] text-amber-600">Giới hạn ADV</div>
                    )}
                  </td>
                  {data.has_current_holdings && (
                    <>
                      <td className="px-4 py-3 text-right font-mono">{position.current_shares.toLocaleString("vi-VN")}</td>
                      <td className={`px-4 py-3 text-right font-mono font-bold ${actionClass(position.action)}`}>
                        {actionLabel(position.action)} {Math.abs(position.delta_shares).toLocaleString("vi-VN")}
                      </td>
                      <td className="px-4 py-3 text-right text-slate-500">{fmtVND(position.trade_value_vnd)}</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <PerformanceSummary data={data} />
    </section>
  );
}

function PerformanceSummary({ data }: { data: FundPlanResult }) {
  const performance = data.summary.strategy_total_return_pct;
  const benchmark = data.summary.benchmark_return_pct;
  const excess = data.summary.excess_return_pct;
  const positive = performance > 0;
  const negative = performance < 0;
  const tone = positive
    ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300"
    : negative
      ? "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-300"
      : "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300";

  return (
    <div className={`rounded-2xl border p-5 ${tone}`}>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide opacity-75">
            {data.trust_tier === "strict_pit"
              ? "Hiệu suất theo sổ nắm giữ từ ngày thực thi"
              : data.trust_tier === "trusted_local"
                ? "Hiệu suất theo dữ liệu đã xác nhận từ ngày thực thi"
                : "Hiệu suất nghiên cứu từ ngày thực thi"}{" "}
            {data.execution_date ?? data.rebalance_date}
          </p>
          <p className="mt-1 text-3xl font-bold">
            {performance > 0 ? "+" : ""}{performance.toFixed(2)}%
          </p>
          <p className="mt-1 text-sm opacity-80">
            100 triệu mô phỏng ban đầu → {fmtVND(data.summary.model_value_per_100m_vnd)}
          </p>
          <p className="mt-1 text-xs opacity-70">
            {data.trust_tier === "strict_pit"
              ? `Cổ tức tiền mặt giữ ở cash: ${data.summary.model_cash_weight_pct.toFixed(2)}%`
              : "Cổ tức/quyền phản ánh theo chuỗi giá điều chỉnh đang lưu; không tách riêng cash."}
          </p>
        </div>
        {benchmark != null && excess != null && (
          <div className="grid min-w-[320px] grid-cols-2 gap-3">
            <div className="rounded-xl border border-current/15 bg-white/60 p-3 dark:bg-slate-950/20">
              <p className="text-xs font-semibold opacity-70">
                {data.summary.benchmark_symbol} đồng kỳ
              </p>
              <p className="mt-1 text-xl font-bold">
                {benchmark > 0 ? "+" : ""}{benchmark.toFixed(2)}%
              </p>
              {data.summary.benchmark_value_per_100m_vnd != null && (
                <p className="mt-1 text-xs opacity-70">
                  100 triệu → {fmtVND(data.summary.benchmark_value_per_100m_vnd)}
                </p>
              )}
            </div>
            <div className="rounded-xl border border-current/15 bg-white/60 p-3 dark:bg-slate-950/20">
              <p className="text-xs font-semibold opacity-70">
                Chênh lệch so với {data.summary.benchmark_symbol}
              </p>
              <p className="mt-1 text-xl font-bold">
                {excess > 0 ? "+" : ""}{excess.toFixed(2)} điểm %
              </p>
              <p className="mt-1 text-xs opacity-70">
                {excess >= 0 ? "Cao hơn chỉ số giá" : "Thấp hơn chỉ số giá"}
              </p>
            </div>
          </div>
        )}
        <div className="text-right text-sm">
          <p>
            <span className="font-semibold">{data.summary.gainers_count}</span> mã tăng
            {" · "}
            <span className="font-semibold">{data.summary.losers_count}</span> mã giảm
            {data.summary.unchanged_count > 0
              ? ` · ${data.summary.unchanged_count} mã không đổi`
              : ""}
          </p>
          <p className="mt-1 text-xs opacity-70">
            {data.trust_tier === "strict_pit"
              ? `Danh mục dùng giá chưa điều chỉnh cùng sổ corporate action đã xác minh; cổ tức tiền mặt không tái đầu tư. ${data.summary.benchmark_symbol} hiện chỉ là chỉ số giá tham khảo, chưa phải total-return authoritative. Chưa gồm phí giao dịch và lãi tiền mặt.`
              : `Danh mục dùng giá mở cửa ngày mua và chuỗi giá điều chỉnh Vietcap (as-of ${data.performance_source_as_of ?? "không rõ"}). ${data.trust_tier === "trusted_local" ? "Dữ liệu này đã được chủ quỹ chấp nhận để sử dụng" : "Đây là kết quả nghiên cứu"}; ${data.summary.benchmark_symbol} là chuỗi đối chiếu cùng nguồn. Chưa gồm phí giao dịch và lãi tiền mặt.`}
          </p>
        </div>
      </div>
    </div>
  );
}

function SortableHeader({
  label,
  sortKey,
  activeKey,
  direction,
  onSort,
  align = "left",
}: {
  label: string;
  sortKey: SortKey;
  activeKey: SortKey | null;
  direction: SortDirection;
  onSort: (key: SortKey) => void;
  align?: "left" | "right";
}) {
  const active = activeKey === sortKey;
  return (
    <th
      className="px-2 py-1"
      aria-sort={active
        ? direction === "asc" ? "ascending" : "descending"
        : "none"}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={`flex w-full items-center gap-1 rounded px-2 py-2 transition hover:bg-slate-200/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:hover:bg-slate-700 ${
          align === "right" ? "justify-end text-right" : "justify-start text-left"
        }`}
        title={active
          ? `Đang sắp xếp ${direction === "asc" ? "tăng dần" : "giảm dần"}`
          : `Sắp xếp theo ${label}`}
      >
        <span>{label}</span>
        <span
          aria-hidden="true"
          className={active ? "text-blue-600 dark:text-blue-400" : "text-slate-300 dark:text-slate-600"}
        >
          {active ? direction === "asc" ? "▲" : "▼" : "↕"}
        </span>
      </button>
    </th>
  );
}

function SummaryCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "buy" | "sell";
}) {
  const color = tone === "buy"
    ? "text-emerald-600 dark:text-emerald-400"
    : tone === "sell" ? "text-rose-600 dark:text-rose-400" : "";
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-900">
      <p className="text-xs text-slate-500">{label}</p>
      <p className={`mt-1 text-lg font-bold ${color}`}>{value}</p>
    </div>
  );
}
