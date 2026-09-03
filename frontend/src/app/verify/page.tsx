"use client";

import { useState } from "react";
import { api, VerifyResult, VerifyComparison } from "@/lib/api";

const STATUS_STYLES: Record<string, string> = {
  ok: "bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-400",
  warning: "bg-yellow-100 dark:bg-yellow-900/30 text-yellow-800 dark:text-yellow-400",
  error: "bg-red-100 dark:bg-red-900/30 text-red-800 dark:text-red-400",
  missing: "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400",
};

function statusBadge(status: string) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_STYLES[status] || "bg-gray-100 dark:bg-gray-800"}`}>
      {status}
    </span>
  );
}

function fmtVal(v: string | null): string {
  if (v === null || v === "None") return "-";
  const n = parseFloat(v);
  if (isNaN(n)) return v;
  if (Math.abs(n) >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
  if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return n.toLocaleString("vi-VN", { maximumFractionDigits: 2 });
}

function diffColor(diff: number | null): string {
  if (diff === null) return "";
  if (diff <= 5) return "text-green-600 dark:text-green-400";
  if (diff <= 10) return "text-yellow-600 dark:text-yellow-400";
  return "text-red-600 dark:text-red-400 font-medium";
}

export default function VerifyPage() {
  const [symbol, setSymbol] = useState("");
  const [year, setYear] = useState("");
  const [result, setResult] = useState<VerifyResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleVerify(e: React.FormEvent) {
    e.preventDefault();
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const yr = year ? parseInt(year) : undefined;
      const res = await api.verify(sym, yr);
      setResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Kiểm tra thất bại");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Search form */}
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-sm dark:shadow-gray-900/20 border border-gray-200 dark:border-gray-700 p-5">
        <h1 className="text-xl font-bold mb-3">Kiểm tra dữ liệu</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
          Đối chiếu dữ liệu tài chính VCI vs KBS để xác minh độ tin cậy
        </p>
        <form onSubmit={handleVerify} className="flex flex-col sm:flex-row gap-3">
          <input
            type="text"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="Symbol (e.g. VNM)"
            className="flex-1 px-4 py-2.5 border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 dark:text-gray-100 focus:ring-2 focus:ring-blue-500 focus:outline-none font-medium"
          />
          <input
            type="number"
            value={year}
            onChange={(e) => setYear(e.target.value)}
            placeholder="Year (optional)"
            className="w-32 px-4 py-2.5 border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 dark:text-gray-100 focus:ring-2 focus:ring-blue-500 focus:outline-none"
          />
          <button
            type="submit"
            disabled={loading || !symbol.trim()}
            className="px-6 py-2.5 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? "Đang kiểm tra..." : "Kiểm tra"}
          </button>
        </form>
        {error && <p className="text-red-500 dark:text-red-400 text-sm mt-2">{error}</p>}
      </div>

      {/* Result */}
      {result && (
        <div className="space-y-4">
          {/* Overall status */}
          <div className="bg-white dark:bg-gray-900 rounded-xl shadow-sm dark:shadow-gray-900/20 border border-gray-200 dark:border-gray-700 p-5">
            <div className="flex flex-wrap items-center gap-4">
              <h2 className="text-lg font-bold">{result.symbol}</h2>
              <span className="text-sm text-gray-500 dark:text-gray-400">Năm: {result.year}</span>
              <div className="flex items-center gap-2">
                <span className="text-sm text-gray-500 dark:text-gray-400">Tổng:</span>
                {statusBadge(result.overall_status)}
              </div>
              <div className="flex gap-3 text-sm ml-auto">
                <span className="text-green-600 dark:text-green-400">{result.ok} Đạt</span>
                <span className="text-yellow-600 dark:text-yellow-400">{result.warnings} Cảnh báo</span>
                <span className="text-red-600 dark:text-red-400">{result.errors} Lỗi</span>
              </div>
            </div>
          </div>

          {/* Comparison table */}
          <div className="bg-white dark:bg-gray-900 rounded-xl shadow-sm dark:shadow-gray-900/20 border border-gray-200 dark:border-gray-700 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-left">
                  <th className="px-4 py-2.5 font-medium text-gray-600 dark:text-gray-300">Chỉ số</th>
                  <th className="px-4 py-2.5 font-medium text-gray-600 dark:text-gray-300 text-right">VCI</th>
                  <th className="px-4 py-2.5 font-medium text-gray-600 dark:text-gray-300 text-right">KBS</th>
                  <th className="px-4 py-2.5 font-medium text-gray-600 dark:text-gray-300 text-right">Chênh lệch %</th>
                  <th className="px-4 py-2.5 font-medium text-gray-600 dark:text-gray-300">Trạng thái</th>
                  <th className="px-4 py-2.5 font-medium text-gray-600 dark:text-gray-300">Ghi chú</th>
                </tr>
              </thead>
              <tbody>
                {result.comparisons.map((c: VerifyComparison) => (
                  <tr key={c.metric} className="border-b border-gray-200 dark:border-gray-700 last:border-0 hover:bg-gray-50 dark:hover:bg-gray-800">
                    <td className="px-4 py-2.5 font-medium capitalize">{c.metric.replace(/_/g, " ")}</td>
                    <td className="px-4 py-2.5 text-right font-mono">{fmtVal(c.vci)}</td>
                    <td className="px-4 py-2.5 text-right font-mono">{fmtVal(c.kbs)}</td>
                    <td className={`px-4 py-2.5 text-right font-mono ${diffColor(c.diff_pct)}`}>
                      {c.diff_pct !== null ? `${c.diff_pct.toFixed(1)}%` : "-"}
                    </td>
                    <td className="px-4 py-2.5">{statusBadge(c.status)}</td>
                    <td className="px-4 py-2.5 text-gray-500 dark:text-gray-400 text-xs">{c.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Legend */}
          <div className="bg-white dark:bg-gray-900 rounded-xl shadow-sm dark:shadow-gray-900/20 border border-gray-200 dark:border-gray-700 p-4 text-xs text-gray-500 dark:text-gray-400">
            <p className="font-medium text-gray-700 dark:text-gray-200 mb-1">Ngưỡng chấp nhận</p>
            <div className="flex flex-wrap gap-x-6 gap-y-1">
              <span>EPS, Revenue, Net profit: &le;5% OK, &le;10% warning</span>
              <span>PE, PB, ROE, BVPS, Market cap: &le;10% OK, &le;20% warning</span>
            </div>
          </div>
        </div>
      )}

      {/* Quick verify hints */}
      {!result && !loading && (
        <div className="bg-white dark:bg-gray-900 rounded-xl shadow-sm dark:shadow-gray-900/20 border border-gray-200 dark:border-gray-700 p-5">
          <h3 className="font-medium text-gray-700 dark:text-gray-200 mb-2">Kiểm tra nhanh</h3>
          <div className="flex flex-wrap gap-2">
            {["VNM", "FPT", "HPG", "MWG", "VIC", "VHM", "TCB", "ACB"].map((s) => (
              <button
                key={s}
                onClick={() => { setSymbol(s); }}
                className="px-3 py-1.5 bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg text-sm font-medium transition-colors"
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
