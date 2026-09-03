"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { api, DbHealth, StreamProgress } from "@/lib/api";
import {
  UpdateProgressPanel,
  UpdateProgress,
  INITIAL_PROGRESS,
} from "./update-progress-panel";

/* ---------- small components ---------- */

function CoverageBar({ covered, total, label }: { covered: number; total: number; label: string }) {
  const pct = total > 0 ? (covered / total) * 100 : 0;
  const color = pct >= 90 ? "bg-green-500" : pct >= 70 ? "bg-yellow-500" : "bg-red-500";
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-gray-600 dark:text-gray-400">{label}</span>
        <span className="font-medium">{covered}/{total} ({pct.toFixed(1)}%)</span>
      </div>
      <div className="h-2.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-all duration-500`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
    </div>
  );
}

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-3 text-center">
      <p className="text-xs text-gray-500 dark:text-gray-400">{label}</p>
      <p className="text-lg font-bold">{value}</p>
      {sub && <p className="text-xs text-gray-400 dark:text-gray-500">{sub}</p>}
    </div>
  );
}

/* ---------- SSE event → progress state mapper ---------- */

function mapSSEtoProgress(
  ev: StreamProgress,
  setProgress: React.Dispatch<React.SetStateAction<UpdateProgress>>,
  unitField: "bars" | "rows",
) {
  if (ev.type === "start") {
    setProgress((p) => ({ ...p, total: ev.total ?? 0 }));
  } else if (ev.type === "progress") {
    const count = unitField === "bars" ? (ev.bars ?? 0) : (ev.rows ?? 0);
    setProgress((p) => ({
      ...p,
      total: ev.total ?? p.total,
      current: (ev.index ?? 0) + 1,
      currentSymbol: ev.symbol ?? "",
      updated: ev.updated ?? p.updated,
      failed: ev.failed ?? p.failed,
      inserted: ev.inserted ?? p.inserted,
      log: [...p.log, {
        symbol: ev.symbol ?? "",
        status: ev.status ?? "ok",
        bars: count,
        error: ev.error ?? undefined,
        skipReason: ev.skip_reason ?? undefined,
      }],
    }));
  } else if (ev.type === "done") {
    setProgress((p) => ({
      ...p, phase: "done",
      remainingMissing: ev.remaining_missing,
      runStatus: ev.run_status,
      message: ev.message,
      broadPriceDate: ev.broad_price_date,
      latestMarketDate: ev.latest_market_date,
      provisionalPrices: ev.provisional_prices,
    }));
  }
}

/* ---------- main page ---------- */

export default function DataPage() {
  const [health, setHealth] = useState<DbHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Price update state
  const [priceProgress, setPriceProgress] = useState<UpdateProgress>(INITIAL_PROGRESS);
  const priceAbortRef = useRef<AbortController | null>(null);

  // Financials update state
  const [finProgress, setFinProgress] = useState<UpdateProgress>(INITIAL_PROGRESS);
  const finAbortRef = useRef<AbortController | null>(null);

  const anyRunning = priceProgress.phase === "running" || finProgress.phase === "running";

  const loadHealth = useCallback(async () => {
    try {
      setError("");
      const h = await api.dbHealth();
      setHealth(h);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Không tải được trạng thái dữ liệu");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadHealth(); }, [loadHealth]);

  // Cleanup: abort running streams on unmount
  useEffect(() => {
    return () => {
      priceAbortRef.current?.abort();
      finAbortRef.current?.abort();
    };
  }, []);

  function handlePriceUpdate() {
    if (priceProgress.phase === "running") {
      priceAbortRef.current?.abort();
      void api.cancelSync();
      setPriceProgress((p) => ({ ...p, phase: "done" }));
      return;
    }
    setPriceProgress({ ...INITIAL_PROGRESS, phase: "running" });
    const ctrl = api.streamPriceUpdate(
      (ev) => mapSSEtoProgress(ev, setPriceProgress, "bars"),
      () => {
        setPriceProgress((p) => ({ ...p, phase: p.phase === "running" ? "done" : p.phase }));
        loadHealth();
      },
    );
    priceAbortRef.current = ctrl;
  }

  function handleFinancialsUpdate() {
    if (finProgress.phase === "running") {
      finAbortRef.current?.abort();
      void api.cancelSync();
      setFinProgress((p) => ({ ...p, phase: "done" }));
      return;
    }
    setFinProgress({ ...INITIAL_PROGRESS, phase: "running" });
    const ctrl = api.streamFinancialsUpdate(
      (ev) => mapSSEtoProgress(ev, setFinProgress, "rows"),
      () => {
        setFinProgress((p) => ({ ...p, phase: p.phase === "running" ? "done" : p.phase }));
        loadHealth();
      },
    );
    finAbortRef.current = ctrl;
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="animate-spin h-8 w-8 border-4 border-blue-600 border-t-transparent rounded-full" />
      </div>
    );
  }

  if (error && !health) {
    return (
      <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl p-6 text-center">
        <p className="text-red-600 dark:text-red-400 font-medium">{error}</p>
        <button onClick={() => { setLoading(true); loadHealth(); }}
          className="text-blue-600 dark:text-blue-400 text-sm mt-2">Thử lại</button>
      </div>
    );
  }

  if (!health) return null;

  return (
    <div className="space-y-4">
      {/* Header + controls */}
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-sm dark:shadow-gray-900/20 border border-gray-200 dark:border-gray-700 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold">Trạng thái dữ liệu</h1>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              {health.total_symbols} mã đang theo dõi
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => { setLoading(true); loadHealth(); }}
              disabled={anyRunning}
              className="px-4 py-2 bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg text-sm font-medium transition-colors disabled:opacity-50">
              Làm mới
            </button>
            <button onClick={handlePriceUpdate}
              disabled={finProgress.phase === "running"}
              className={`px-4 py-2 rounded-lg font-medium text-sm transition-colors disabled:opacity-50 ${
                priceProgress.phase === "running"
                  ? "bg-red-600 hover:bg-red-700 text-white"
                  : "bg-blue-600 hover:bg-blue-700 text-white"
              }`}>
              {priceProgress.phase === "running" ? "Hủy" : "Cập nhật giá"}
            </button>
            <button onClick={handleFinancialsUpdate}
              disabled={priceProgress.phase === "running"}
              className={`px-4 py-2 rounded-lg font-medium text-sm transition-colors disabled:opacity-50 ${
                finProgress.phase === "running"
                  ? "bg-red-600 hover:bg-red-700 text-white"
                  : "bg-emerald-600 hover:bg-emerald-700 text-white"
              }`}>
              {finProgress.phase === "running" ? "Hủy" : "Cập nhật BCTC"}
            </button>
          </div>
        </div>

        {/* Price update progress */}
        <UpdateProgressPanel progress={priceProgress} label="Giá" unitLabel="bars" accentColor="blue" />

        {/* Financials update progress */}
        <UpdateProgressPanel progress={finProgress} label="BCTC" unitLabel="dòng" accentColor="emerald" />
      </div>

      {/* Overview stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3">
        <StatCard label="Tổng mã" value={health.total_symbols} />
        <StatCard label="Đủ phiên mới nhất" value={health.price.completed_session_symbols ?? 0} sub={`${health.price.coverage_pct}% · ${health.price.completed_session ?? "-"}`} />
        <StatCard label="Đã từng có giá" value={health.price.symbols_covered} sub={`${health.price.historical_coverage_pct ?? 0}% lịch sử`} />
        <StatCard label="Giá tạm thời" value={health.price.provisional?.symbols ?? 0} sub={health.price.provisional?.latest ?? "Không có"} />
        <StatCard label="Chậm TT" value={health.price.behind_count}
          sub={health.price.behind_count === 0 ? "Đã cập nhật" : "Cần cập nhật"} />
        <StatCard label="Ngưng GD" value={health.price.stale_count}
          sub={health.price.stale_count === 0 ? "Không có" : "Không có dữ liệu"} />
        <StatCard label="Có BCTC" value={health.financials.symbols_covered} sub={`${health.financials.coverage_pct}%`} />
        <StatCard label="Thiếu BCTC" value={health.financials.missing_count}
          sub={`Năm ${health.financials.check_year}`} />
        <StatCard label="Fallback KBS" value={health.fallback_available ? "Sẵn sàng" : "Không khả dụng"}
          sub={health.fallback_available ? "Đã smoke test" : "Sẽ dừng nếu Vietcap lỗi"} />
      </div>

      {/* Coverage bars */}
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-sm dark:shadow-gray-900/20 border border-gray-200 dark:border-gray-700 p-5 space-y-4">
        <h2 className="font-bold text-sm uppercase tracking-wide text-gray-500 dark:text-gray-400">Độ phủ</h2>
        <CoverageBar covered={health.price.completed_session_symbols ?? 0} total={health.total_symbols} label={`Đủ giá phiên hoàn tất ${health.price.completed_session ?? "-"}`} />
        <CoverageBar covered={health.price.symbols_covered} total={health.total_symbols} label="Đã từng có dữ liệu giá lịch sử" />
        <CoverageBar covered={health.financials.symbols_covered} total={health.total_symbols} label={`Chỉ số tài chính (${health.financials.check_year})`} />
        {health.exchanges.map((ex) => (
          <CoverageBar key={ex.exchange} covered={ex.with_price} total={ex.total} label={`Giá ${ex.exchange}`} />
        ))}
      </div>

      {/* Date range info */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="bg-white dark:bg-gray-900 rounded-xl shadow-sm dark:shadow-gray-900/20 border border-gray-200 dark:border-gray-700 p-5">
          <h2 className="font-bold text-sm uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-3">Dữ liệu giá</h2>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-500 dark:text-gray-400">Khoảng ngày</span>
              <span className="font-medium font-mono">{health.price.earliest_date || "-"} &rarr; {health.price.latest_date || "-"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500 dark:text-gray-400">Tổng dòng</span>
              <span className="font-medium">{health.price.total_rows.toLocaleString()}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500 dark:text-gray-400">Mã có dữ liệu</span>
              <span className="font-medium">{health.price.symbols_covered} / {health.total_symbols}</span>
            </div>
            <div className="flex justify-between">
              <span className={health.price.behind_count > 0 ? "text-orange-600 dark:text-orange-400" : "text-green-600 dark:text-green-400"}>
                Chậm thị trường
              </span>
              <span className="font-medium">{health.price.behind_count}</span>
            </div>
            <div className="flex justify-between">
              <span className={health.price.stale_count > 0 ? "text-gray-500 dark:text-gray-400" : "text-green-600 dark:text-green-400"}>
                Ngưng GD / Kém thanh khoản
              </span>
              <span className="font-medium">{health.price.stale_count}</span>
            </div>
          </div>
        </div>
        <div className="bg-white dark:bg-gray-900 rounded-xl shadow-sm dark:shadow-gray-900/20 border border-gray-200 dark:border-gray-700 p-5">
          <h2 className="font-bold text-sm uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-3">Chỉ số tài chính</h2>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-500 dark:text-gray-400">Khoảng năm</span>
              <span className="font-medium font-mono">{health.financials.earliest_year || "-"} &rarr; {health.financials.latest_year || "-"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500 dark:text-gray-400">Tổng dòng</span>
              <span className="font-medium">{health.financials.total_rows.toLocaleString()}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500 dark:text-gray-400">Mã có dữ liệu</span>
              <span className="font-medium">{health.financials.symbols_covered} / {health.total_symbols}</span>
            </div>
            <div className="flex justify-between">
              <span className={health.financials.missing_count > 0 ? "text-orange-600 dark:text-orange-400" : "text-green-600 dark:text-green-400"}>
                Thiếu ({health.financials.check_year})
              </span>
              <span className="font-medium">{health.financials.missing_count}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Missing symbols lists */}
      {health.price.behind_count > 0 && (
        <details className="bg-white dark:bg-gray-900 rounded-xl shadow-sm dark:shadow-gray-900/20 border border-gray-200 dark:border-gray-700">
          <summary className="px-5 py-3 font-medium cursor-pointer hover:text-blue-600 dark:hover:text-blue-400 text-sm">
            Chậm thị trường ({health.price.behind_count} mã — cần cập nhật)
          </summary>
          <div className="px-5 pb-4">
            <div className="flex flex-wrap gap-1.5">
              {health.price.behind_symbols.map((s) => (
                <span key={s} className="px-2 py-0.5 bg-orange-50 dark:bg-orange-900/20 text-orange-700 dark:text-orange-400 rounded text-xs font-mono">{s}</span>
              ))}
            </div>
          </div>
        </details>
      )}

      {health.price.stale_count > 0 && (
        <details className="bg-white dark:bg-gray-900 rounded-xl shadow-sm dark:shadow-gray-900/20 border border-gray-200 dark:border-gray-700">
          <summary className="px-5 py-3 font-medium cursor-pointer hover:text-gray-600 dark:hover:text-gray-400 text-sm">
            Ngưng GD / Kém thanh khoản ({health.price.stale_count} mã — không có dữ liệu mới)
          </summary>
          <div className="px-5 pb-4">
            <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">
              Các mã chậm 3+ ngày giao dịch. VCI/KBS không có dữ liệu mới — có thể đã ngưng GD hoặc rất kém thanh khoản.
            </p>
            <div className="flex flex-wrap gap-1.5">
              {health.price.stale_symbols.map((s) => (
                <span key={s} className="px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 rounded text-xs font-mono">{s}</span>
              ))}
            </div>
          </div>
        </details>
      )}

      {health.financials.missing_count > 0 && (
        <details className="bg-white dark:bg-gray-900 rounded-xl shadow-sm dark:shadow-gray-900/20 border border-gray-200 dark:border-gray-700">
          <summary className="px-5 py-3 font-medium cursor-pointer hover:text-blue-600 dark:hover:text-blue-400 text-sm">
            Thiếu chỉ số tài chính ({health.financials.missing_count} mã, năm {health.financials.check_year})
          </summary>
          <div className="px-5 pb-4">
            <div className="flex flex-wrap gap-1.5">
              {health.financials.missing_symbols.map((s) => (
                <span key={s} className="px-2 py-0.5 bg-orange-50 dark:bg-orange-900/20 text-orange-700 dark:text-orange-400 rounded text-xs font-mono">{s}</span>
              ))}
            </div>
          </div>
        </details>
      )}

      {/* All good */}
      {health.price.behind_count === 0 && health.price.stale_count === 0 && health.financials.missing_count === 0 && (
        <div className="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-xl p-5 text-center">
          <p className="text-green-700 dark:text-green-400 font-medium">Tất cả dữ liệu đã được cập nhật</p>
        </div>
      )}
    </div>
  );
}
