"use client";

import { useEffect, useRef, useState } from "react";
import {
  api,
  FundPlanResult,
  FundPreference,
  SyncStatus,
} from "@/lib/api";
import {
  HoldingsEditor,
  holdingsToText,
  parseHoldingsText,
} from "./holdings-editor";
import { PortfolioResults } from "./portfolio-results";

const SELECT_PCTS: FundPreference["select_pct"][] = [10, 12, 14, 16];
const wait = (milliseconds: number) =>
  new Promise((resolve) => window.setTimeout(resolve, milliseconds));

export default function Dashboard() {
  const [strategy, setStrategy] = useState<FundPreference["strategy"]>("LAST_8Q_PLUS");
  const [selectPct, setSelectPct] = useState<FundPreference["select_pct"]>(10);
  const [nav, setNav] = useState("");
  const [unit, setUnit] = useState<"million" | "billion">("million");
  const [holdingsText, setHoldingsText] = useState("");
  const [holdingsUpdatedAt, setHoldingsUpdatedAt] = useState<string | null>(null);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [plan, setPlan] = useState<FundPlanResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const cancelRequested = useRef(false);
  const investmentReady =
    syncStatus?.strategy_snapshot?.investment_ready === true;
  const researchPlannerReady =
    syncStatus?.legacy_research_planner_enabled === true
    && syncStatus?.strategy_snapshot?.research_planner_available === true;
  const userConfirmedReady =
    syncStatus?.strategy_snapshot?.user_confirmed_ready === true;
  const plannerReady =
    investmentReady || userConfirmedReady || researchPlannerReady;

  useEffect(() => {
    Promise.all([
      api.fundPreferences(),
      api.fundHoldings(),
      api.syncStatus(),
    ]).then(([preferences, holdings, status]) => {
      setStrategy(preferences.strategy);
      setSelectPct(preferences.select_pct);
      setHoldingsText(holdingsToText(holdings.holdings));
      setHoldingsUpdatedAt(holdings.updated_at);
      setSyncStatus(status);
    }).catch(() => {
      setError("Không thể tải cấu hình đã lưu. Hãy kiểm tra backend.");
    });
  }, []);

  useEffect(() => {
    if (!syncStatus?.running) return;
    const timer = window.setInterval(() => {
      api.syncStatus().then(setSyncStatus).catch(() => undefined);
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [syncStatus?.running]);

  async function saveHoldings() {
    setSaving(true);
    setError("");
    try {
      const parsed = parseHoldingsText(holdingsText);
      const saved = await api.saveFundHoldings(parsed);
      setHoldingsText(holdingsToText(saved.holdings));
      setHoldingsUpdatedAt(saved.updated_at);
    } catch (unknownError) {
      setError(unknownError instanceof Error ? unknownError.message : "Không thể lưu danh mục");
    } finally {
      setSaving(false);
    }
  }

  async function calculatePortfolio() {
    setError("");
    setPlan(null);
    if (!plannerReady) {
      const issues =
        syncStatus?.strategy_snapshot?.blocking_issues?.join(", ")
        ?? "SNAPSHOT_NOT_VERIFIED";
      setError(
        `Hệ thống đang khóa lập danh mục vì dữ liệu chưa được xác minh đầy đủ: ${issues}`,
      );
      return;
    }
    const rawNav = Number(nav);
    const navVnd = rawNav * (unit === "million" ? 1_000_000 : 1_000_000_000);
    if (!Number.isFinite(navVnd) || navVnd <= 0) {
      setError("NAV phải là một số dương.");
      return;
    }

    let currentHoldings;
    try {
      currentHoldings = parseHoldingsText(holdingsText);
    } catch (unknownError) {
      setError(unknownError instanceof Error ? unknownError.message : "Danh mục hiện tại không hợp lệ");
      return;
    }

    setLoading(true);
    cancelRequested.current = false;
    const waitDeadline = Date.now() + 15 * 60 * 1000;
    try {
      setProgress("Đang kiểm tra độ mới của dữ liệu...");
      let status = await api.syncStatus();
      // Research planning refreshes only the selected symbols inside the
      // planner API. It must not trigger a slow full-universe/PIT rebuild.
      const plannerNeedsSync =
        !researchPlannerReady && !userConfirmedReady && status.needs_sync;
      if (plannerNeedsSync && !status.running) {
        setProgress("Dữ liệu cũ — đang khởi động cập nhật giá và báo cáo tài chính...");
        await api.startSync();
        await wait(500);
        status = await api.syncStatus();
      }
      while (status.running) {
        if (cancelRequested.current) {
          throw new Error("Đã dừng chờ đồng bộ. Tiến trình nền đang nhận yêu cầu hủy an toàn.");
        }
        if (Date.now() > waitDeadline) {
          throw new Error("Đồng bộ kéo dài quá 15 phút. Có thể tiếp tục theo dõi ở trang Dữ liệu hoặc yêu cầu hủy.");
        }
        const run = status.last_run;
        const isFinancial = run?.stage?.startsWith("financials") ?? false;
        const isSnapshot = run?.stage === "backtest_snapshots";
        const isAdjusted = run?.stage === "adjusted_prices";
        if (isAdjusted) {
          setProgress("Đang cập nhật giá điều chỉnh cổ tức và quyền...");
          setSyncStatus(status);
          await wait(2_000);
          status = await api.syncStatus();
          continue;
        }
        const stage = isSnapshot
          ? "backtest và khóa snapshot chiến lược"
          : isFinancial ? "báo cáo tài chính" : "giá";
        const updated = isSnapshot
          ? run?.financials_updated ?? 0
          : isFinancial
            ? run?.financials_updated ?? 0
            : run?.prices_processed ?? run?.prices_updated ?? 0;
        const total = isFinancial
          ? run?.financial_symbols_total
          : isSnapshot ? undefined : run?.price_symbols_total;
        setProgress(
          `Đang cập nhật ${stage}: đã xử lý ${updated}${total ? `/${total}` : ""} mã...`,
        );
        setSyncStatus(status);
        await wait(2_000);
        status = await api.syncStatus();
      }
      setSyncStatus(status);
      if (status.last_run?.status === "failed") {
        throw new Error(
          status.last_run.message
            ? `Đồng bộ dữ liệu thất bại: ${status.last_run.message}`
            : "Đồng bộ dữ liệu thất bại. Hãy thử lại.",
        );
      }

      setProgress("Đang làm mới giá và quy đổi danh mục từ ngày chiến lược...");
      const result = await api.portfolioPlan({
        nav_vnd: navVnd,
        strategy,
        select_pct: selectPct,
        holdings: currentHoldings,
        auto_sync: !researchPlannerReady,
      });
      setPlan(result);
      setSyncStatus(await api.syncStatus());
    } catch (unknownError) {
      setError(unknownError instanceof Error ? unknownError.message : "Không thể tính danh mục");
    } finally {
      setLoading(false);
      setProgress("");
    }
  }

  async function cancelCalculation() {
    cancelRequested.current = true;
    try {
      await api.cancelSync();
    } catch {
      // Stopping the browser wait is still safe if no background job exists.
    }
  }

  const strictBacktest = syncStatus?.strategy_snapshot?.backtests?.find(
    (item) =>
      item.strategy === strategy
      && item.select_pct === selectPct
      && item.pit_tier === "strict_pit",
  );
  const researchBacktest = syncStatus?.strategy_snapshot?.backtests?.find(
    (item) =>
      item.strategy === strategy
      && item.select_pct === selectPct
      && item.pit_tier === "legacy_research",
  );
  const userConfirmedBacktest =
    syncStatus?.strategy_snapshot?.backtests?.find(
      (item) =>
        item.strategy === strategy
        && item.select_pct === selectPct
        && item.pit_tier === "trusted_local",
    );

  return (
    <div className="space-y-6">
      <section className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-900">
        <div className="border-b border-slate-100 bg-gradient-to-r from-blue-50 to-indigo-50 px-6 py-7 dark:border-slate-800 dark:from-blue-950/40 dark:to-indigo-950/30">
          <p className="text-sm font-semibold uppercase tracking-widest text-blue-600 dark:text-blue-400">
            PE5Y Fund Planner
          </p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight">
            Lập danh mục theo NAV hiện tại
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-600 dark:text-slate-300">
            Chọn chiến lược, nhập NAV và nhận danh mục như thể quỹ đã mua từ
            ngày bắt đầu chu kỳ rồi nắm giữ đến hiện tại.
          </p>
        </div>

        <div className="grid gap-5 p-6 lg:grid-cols-[1fr_1fr_1.1fr_auto] lg:items-end">
          <div className="block">
            <span className="mb-2 flex items-center justify-between text-sm font-medium">
              <label htmlFor="strategy-select">Chiến lược</label>
              <button
                type="button"
                onClick={() => document.getElementById("strategy-select")?.focus()}
                className="text-xs font-semibold text-blue-600 hover:text-blue-700"
              >
                Đổi chiến lược
              </button>
            </span>
            <select
              id="strategy-select"
              value={strategy}
              onChange={(event) => setStrategy(event.target.value as FundPreference["strategy"])}
              className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 dark:border-slate-700 dark:bg-slate-950"
            >
              <option value="LAST_8Q_PLUS">LAST 8Q+ · Chất lượng chặt</option>
              <option value="TTM_20Q">TTM 20Q · Phạm vi rộng</option>
            </select>
          </div>

          <label className="block" htmlFor="select-pct">
            <span className="mb-2 block text-sm font-medium">Tỷ lệ sàng lọc</span>
            <select
              id="select-pct"
              value={selectPct}
              onChange={(event) => setSelectPct(Number(event.target.value) as FundPreference["select_pct"])}
              className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 dark:border-slate-700 dark:bg-slate-950"
            >
              {SELECT_PCTS.map((pct) => (
                <option key={pct} value={pct}>Top {pct}%</option>
              ))}
            </select>
          </label>

          <div className="block">
            <label htmlFor="fund-nav" className="mb-2 block text-sm font-medium">NAV hiện tại</label>
            <div className="flex">
              <input
                id="fund-nav"
                aria-describedby="fund-nav-unit"
                type="number"
                value={nav}
                onChange={(event) => setNav(event.target.value)}
                min="0"
                step="1"
                className="min-w-0 flex-1 rounded-l-xl border border-r-0 border-slate-200 px-4 py-3 text-lg font-semibold outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-950"
              />
              <select
                id="fund-nav-unit"
                aria-label="Đơn vị NAV"
                value={unit}
                onChange={(event) => setUnit(event.target.value as "million" | "billion")}
                className="rounded-r-xl border border-slate-200 bg-slate-50 px-3 py-3 text-sm dark:border-slate-700 dark:bg-slate-800"
              >
                <option value="million">triệu</option>
                <option value="billion">tỷ</option>
              </select>
            </div>
          </div>

          <button
            type="button"
            onClick={calculatePortfolio}
            disabled={loading || !plannerReady}
            title={
              plannerReady
                ? undefined
                : "Chưa có snapshot đạt chuẩn đầu tư"
            }
            className="rounded-xl bg-blue-600 px-6 py-3.5 font-semibold text-white shadow-sm hover:bg-blue-700 disabled:cursor-wait disabled:opacity-60"
          >
            {loading ? "Đang tính..." : "Tính danh mục"}
          </button>
        </div>

        <div className="flex flex-wrap gap-x-5 gap-y-1 px-6 pb-5 text-xs text-slate-500">
          <span>Chiến lược và tỷ lệ được ghi nhớ sau khi tính.</span>
          <span>
            Dữ liệu giá: <b>{syncStatus?.broad_price_date ?? "đang kiểm tra"}</b>
          </span>
          <span>
            Snapshot chiến lược:{" "}
            <b>
              {syncStatus?.strategy_snapshot
                ? `#${syncStatus.strategy_snapshot.snapshot_set_id}`
                : "chưa sẵn sàng"}
            </b>
          </span>
          {syncStatus?.financial_version && (
            <span>
              Dữ liệu tài chính:{" "}
              <b>
                v{syncStatus.financial_version.id}
                {syncStatus.financial_version.official_provenance_ready
                  ? syncStatus.strategy_snapshot?.strict_coverage?.first_hold_year
                    ? ` · strict PIT từ chu kỳ ${syncStatus.strategy_snapshot.strict_coverage.first_hold_year}`
                    : " · provenance tài liệu gốc đã xác minh"
                  : userConfirmedReady
                    ? " · dữ liệu cục bộ đã được chủ quỹ xác nhận"
                  : " · vendor research, chưa đủ provenance chính thức"}
              </b>
            </span>
          )}
          {strictBacktest && investmentReady && (
            <span>
              Strict PIT {strictBacktest.authoritative ? "authoritative" : "nghiên cứu"}:{" "}
              <b>{(strictBacktest.net_cagr * 100).toFixed(2)}% CAGR</b>
              {" "}· {strictBacktest.cycle_count} chu kỳ
            </span>
          )}
          {userConfirmedBacktest && userConfirmedReady && (
            <span title="Kết quả dùng dữ liệu cục bộ đã được chủ quỹ xác nhận">
              Backtest theo dữ liệu đã xác nhận:{" "}
              <b>{(userConfirmedBacktest.net_cagr * 100).toFixed(2)}% CAGR</b>
              {" "}· {userConfirmedBacktest.cycle_count} chu kỳ
            </span>
          )}
          {researchBacktest && (
            <span title="Các năm cũ dùng giả định độ trễ công bố; không phải kết quả strict PIT">
              Nghiên cứu lịch sử:{" "}
              <b>{(researchBacktest.net_cagr * 100).toFixed(2)}% CAGR</b>
              {" "}· {researchBacktest.cycle_count} chu kỳ
            </span>
          )}
          {syncStatus?.running && <span className="text-blue-600">Đang cập nhật toàn bộ dữ liệu nền</span>}
        </div>
      </section>

      {!plannerReady && syncStatus?.strategy_snapshot && (
        <div className="rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950/30 dark:text-red-200" role="alert">
          <p className="font-semibold">
            Lập danh mục và xuất CSV đang bị khóa để bảo vệ an toàn đầu tư.
          </p>
          <p className="mt-1">
            Snapshot #{syncStatus.strategy_snapshot.snapshot_set_id} ở trạng thái{" "}
            <b>{syncStatus.strategy_snapshot.lifecycle_status ?? "blocked"}</b>.
            {" "}Lỗi chặn:{" "}
            {(syncStatus.strategy_snapshot.blocking_issues ?? ["SNAPSHOT_NOT_VERIFIED"]).join(", ")}.
          </p>
        </div>
      )}

      {plannerReady && (
        <details className="rounded-xl border border-slate-200 bg-white text-sm shadow-sm dark:border-slate-700 dark:bg-slate-900">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-slate-700 marker:content-none dark:text-slate-200">
            <span
              className={`h-2.5 w-2.5 shrink-0 rounded-full ${
                syncStatus?.prices_need_sync
                  ? "bg-amber-500"
                  : "bg-emerald-500"
              }`}
            />
            <span className="font-semibold">
              {userConfirmedReady && !investmentReady
                ? "Dữ liệu cục bộ đã được chủ quỹ xác nhận"
                : researchPlannerReady && !investmentReady
                  ? "Đang dùng chế độ nghiên cứu"
                  : "Dữ liệu lập danh mục đã sẵn sàng"}
            </span>
            <span className="ml-auto text-xs text-slate-500">
              Xem ghi chú dữ liệu
            </span>
          </summary>
          <div className="space-y-2 border-t border-slate-200 px-4 py-3 text-slate-600 dark:border-slate-700 dark:text-slate-300">
            {userConfirmedReady && !investmentReady && (
              <p>
                Danh sách mã, thứ hạng và giá mua đã được khóa trong snapshot{" "}
                #{syncStatus?.strategy_snapshot?.snapshot_set_id}. Hệ thống chưa
                đối chiếu lại từng tài liệu công bố chính thức.
              </p>
            )}
            {researchPlannerReady && !investmentReady && (
              <p>
                Hệ thống dùng snapshot vendor bất biến trong vietnam_stocks.db.
                Kết quả và CSV được ghi nhãn legacy_research.
              </p>
            )}
            {investmentReady && syncStatus?.strategy_snapshot?.backtest_ready !== 1 && (
              <p>
                Backtest strict 10 năm và benchmark total-return chưa hoàn tất
                kiểm định; CAGR đang hiển thị là kết quả nghiên cứu.
              </p>
            )}
            <p>
              {syncStatus?.fallback_available === false
                ? "Nguồn giá dự phòng KBS hiện không khả dụng. Nếu Vietcap lỗi, hệ thống sẽ dừng tính thay vì trộn dữ liệu."
                : "KBS chỉ được dùng để kiểm tra chéo; hệ thống không tự động trộn hai nguồn giá."}
            </p>
            {syncStatus?.prices_need_sync && (
              <p className="font-medium text-amber-700 dark:text-amber-300">
                Dữ liệu toàn thị trường còn mã cần cập nhật. Khi lập danh mục,
                hệ thống vẫn kiểm tra riêng toàn bộ mã được chọn trước khi tính.
              </p>
            )}
          </div>
        </details>
      )}

      <HoldingsEditor
        value={holdingsText}
        onChange={setHoldingsText}
        updatedAt={holdingsUpdatedAt}
        saving={saving}
        onSave={saveHoldings}
        onClear={() => setHoldingsText("")}
      />

      {progress && (
        <div className="flex items-center gap-3 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-700 dark:border-blue-800 dark:bg-blue-950/30 dark:text-blue-300" role="status">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-blue-600 border-t-transparent" />
          <span className="flex-1">{progress}</span>
          <button type="button" onClick={cancelCalculation} className="rounded-lg border border-blue-300 px-3 py-1 font-semibold hover:bg-blue-100 dark:border-blue-700 dark:hover:bg-blue-900">
            Dừng
          </button>
        </div>
      )}
      {error && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-300">
          {error}
        </div>
      )}
      {plan && <PortfolioResults data={plan} />}
    </div>
  );
}
