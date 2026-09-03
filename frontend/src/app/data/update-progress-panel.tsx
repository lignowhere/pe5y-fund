"use client";

import { useEffect, useRef } from "react";

export interface LogEntry {
  symbol: string;
  status: string;
  bars: number;
  error?: string;
  skipReason?: string;
}

export interface UpdateProgress {
  phase: "idle" | "running" | "done";
  total: number;
  current: number;
  currentSymbol: string;
  updated: number;
  failed: number;
  inserted: number;
  remainingMissing?: number;
  runStatus?: string;
  message?: string | null;
  broadPriceDate?: string | null;
  latestMarketDate?: string | null;
  provisionalPrices?: {
    rows: number;
    symbols: number;
    latest: string | null;
  };
  log: LogEntry[];
}

export const INITIAL_PROGRESS: UpdateProgress = {
  phase: "idle", total: 0, current: 0, currentSymbol: "",
  updated: 0, failed: 0, inserted: 0, log: [],
};

/* Static Tailwind class map — dynamic interpolation breaks purge */
const ACCENT = {
  blue: {
    bar: "bg-blue-500",
    bg: "bg-blue-50 dark:bg-blue-900/20",
    text: "text-blue-600 dark:text-blue-400",
    bold: "text-blue-700 dark:text-blue-300",
    symbol: "font-mono font-bold text-blue-600 dark:text-blue-400",
  },
  emerald: {
    bar: "bg-emerald-500",
    bg: "bg-emerald-50 dark:bg-emerald-900/20",
    text: "text-emerald-600 dark:text-emerald-400",
    bold: "text-emerald-700 dark:text-emerald-300",
    symbol: "font-mono font-bold text-emerald-600 dark:text-emerald-400",
  },
} as const;

type AccentKey = keyof typeof ACCENT;

interface Props {
  progress: UpdateProgress;
  label: string;       // "Prices" or "Financials"
  unitLabel: string;   // "bars" or "rows"
  accentColor: AccentKey;
}

export function UpdateProgressPanel({ progress, label, unitLabel, accentColor }: Props) {
  const logEndRef = useRef<HTMLDivElement>(null);
  const a = ACCENT[accentColor];

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [progress.log.length]);

  if (progress.phase === "idle") return null;

  const pct = progress.total > 0 ? (progress.current / progress.total) * 100 : 0;
  const runFailed = progress.phase === "done" && Boolean(
    progress.runStatus && progress.runStatus !== "completed"
  );
  const hasWarning = progress.phase === "done" && !runFailed && Boolean(progress.message);
  const provisionalLatest = progress.provisionalPrices?.latest;
  const hasProvisionalUpdate = Boolean(
    progress.phase === "done" && provisionalLatest && provisionalLatest !== progress.broadPriceDate
  );
  const barBg = progress.phase === "done"
    ? runFailed ? "bg-red-500" : hasWarning ? "bg-amber-500" : "bg-green-500"
    : a.bar;
  const skipped = progress.log.filter(e => e.status === "skip").length;

  return (
    <div className="mt-4 space-y-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
        Cập nhật {label}
      </p>

      {/* Progress bar */}
      <div>
        <div className="flex justify-between text-xs mb-1">
          <span className="text-gray-600 dark:text-gray-400">
            {progress.phase === "running" ? (
              <>Đang tải <span className={a.symbol}>{progress.currentSymbol}</span>...</>
            ) : "Hoàn tất"}
          </span>
          <span className="font-medium font-mono">
            {progress.current}/{progress.total} ({pct.toFixed(0)}%)
          </span>
        </div>
        <div className="h-3 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
          <div className={`h-full rounded-full transition-all duration-300 ${barBg}`}
            style={{ width: `${pct}%` }} />
        </div>
      </div>

      {/* Live counters */}
      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="bg-green-50 dark:bg-green-900/20 rounded-lg p-2">
          <p className="text-xs text-green-600 dark:text-green-400">OK</p>
          <p className="text-lg font-bold text-green-700 dark:text-green-300">{progress.updated}</p>
        </div>
        <div className="bg-red-50 dark:bg-red-900/20 rounded-lg p-2">
          <p className="text-xs text-red-600 dark:text-red-400">Lỗi</p>
          <p className="text-lg font-bold text-red-700 dark:text-red-300">{progress.failed}</p>
        </div>
        <div className={`${a.bg} rounded-lg p-2`}>
          <p className={`text-xs ${a.text}`}>Dòng</p>
          <p className={`text-lg font-bold ${a.bold}`}>{progress.inserted.toLocaleString()}</p>
        </div>
      </div>

      {/* Live log */}
      {progress.log.length > 0 && (
        <div className="bg-gray-50 dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 max-h-48 overflow-y-auto">
          <div className="p-2 space-y-0.5 text-xs font-mono">
            {progress.log.map((entry, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                  entry.status === "ok" ? "bg-green-500" :
                  entry.status === "skip" ? "bg-gray-400" : "bg-red-500"
                }`} />
                <span className="w-10 font-bold">{entry.symbol}</span>
                {entry.status === "ok" && (
                  <span className="text-green-600 dark:text-green-400">+{entry.bars} {unitLabel}</span>
                )}
                {entry.status === "skip" && (
                  <span className="text-gray-400">{entry.skipReason || "không có dữ liệu mới"}</span>
                )}
                {entry.status === "error" && (
                  <span className="text-red-500 dark:text-red-400 truncate">{entry.error}</span>
                )}
              </div>
            ))}
            <div ref={logEndRef} />
          </div>
        </div>
      )}

      {/* Done summary */}
      {progress.phase === "done" && progress.total > 0 && (
        <div className={`border rounded-lg p-3 text-sm ${
          runFailed
            ? "bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800 text-red-700 dark:text-red-400"
            : hasWarning
              ? "bg-yellow-50 dark:bg-yellow-900/20 border-yellow-200 dark:border-yellow-800 text-yellow-700 dark:text-yellow-400"
              : progress.updated > 0
                ? "bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800 text-green-700 dark:text-green-400"
                : "bg-yellow-50 dark:bg-yellow-900/20 border-yellow-200 dark:border-yellow-800 text-yellow-700 dark:text-yellow-400"
        }`}>
          <p className="font-medium">
            {runFailed
              ? "Đồng bộ chưa hoàn tất."
              : hasWarning
                ? "Hoàn tất có cảnh báo."
                : progress.updated > 0
              ? `Xong! ${progress.updated} mã đã cập nhật, ${progress.inserted.toLocaleString()} dòng thêm mới.`
              : `Xong! Tất cả ${progress.total} mã đều đã cập nhật.`}
          </p>
          {(runFailed || hasWarning) && progress.message && (
            <p className="mt-1">{progress.message}</p>
          )}
          {progress.failed > 0 && (
            <p className="text-red-600 dark:text-red-400 mt-1">
              {progress.failed} lỗi — xem log để biết chi tiết.
            </p>
          )}
          {skipped > 0 && (
            <p className="text-gray-500 dark:text-gray-400 mt-1">
              {skipped} bỏ qua (nguồn không có dữ liệu mới hoặc đã tồn tại).
            </p>
          )}
          {progress.remainingMissing != null && progress.remainingMissing > 0 && (
            <p className="text-gray-500 dark:text-gray-400 mt-1">
              {progress.remainingMissing} mã vẫn còn thiếu.
            </p>
          )}
          {progress.remainingMissing === 0 && (
            <p className="mt-1">Tất cả dữ liệu đã được cập nhật!</p>
          )}
          {hasProvisionalUpdate && provisionalLatest && (
            <p className="mt-1">
              Giá ngày {provisionalLatest} đã tải về nhưng đang ở trạng thái tạm thời; hệ thống sẽ chuyển sang dữ liệu chính thức sau 18:30 (giờ Việt Nam).
            </p>
          )}
        </div>
      )}
      {progress.phase === "done" && progress.total === 0 && (
        <div className={`border rounded-lg p-3 text-sm ${
          runFailed
            ? "bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800 text-red-700 dark:text-red-400"
            : hasWarning
              ? "bg-yellow-50 dark:bg-yellow-900/20 border-yellow-200 dark:border-yellow-800 text-yellow-700 dark:text-yellow-400"
              : "bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800 text-green-700 dark:text-green-400"
        }`}>
          {runFailed || hasWarning ? (
            <>
              <p className="font-medium">{runFailed ? "Đồng bộ chưa hoàn tất." : "Hoàn tất có cảnh báo."}</p>
              {progress.message && <p className="mt-1">{progress.message}</p>}
            </>
          ) : (
            <>Không có mã nào cần cập nhật — dữ liệu {label} đã đầy đủ.</>
          )}
        </div>
      )}
    </div>
  );
}
