/** Shared formatting utilities for VND currency display. */

export function fmtVND(v: number): string {
  if (v >= 1e12) return `${(v / 1e12).toFixed(1)}T`;
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(0)}M`;
  return v.toLocaleString("vi-VN");
}

export function fmtPrice(v: number): string {
  return (v / 1000).toFixed(1) + "k";
}

export function fillColor(rate: number): string {
  if (rate >= 0.9) return "text-green-600 dark:text-green-400";
  if (rate >= 0.8) return "text-yellow-600 dark:text-yellow-400";
  return "text-red-600 dark:text-red-400";
}

export function gainColor(pct: number | null): string {
  if (pct === null) return "text-gray-400 dark:text-gray-500";
  if (pct > 0) return "text-green-600 dark:text-green-400";
  if (pct < 0) return "text-red-600 dark:text-red-400";
  return "text-gray-500 dark:text-gray-400";
}

export function fmtGain(pct: number | null): string {
  if (pct === null) return "-";
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(1)}%`;
}

const MONTH_ABBR = ["", "Thg1", "Thg2", "Thg3", "Thg4", "Thg5", "Thg6", "Thg7", "Thg8", "Thg9", "Thg10", "Thg11", "Thg12"];

export function monthName(m: number): string {
  return MONTH_ABBR[m] || `M${m}`;
}
