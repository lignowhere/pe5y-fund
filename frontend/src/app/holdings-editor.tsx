"use client";

import { FundHolding } from "@/lib/api";

export function parseHoldingsText(text: string): FundHolding[] {
  const combined = new Map<string, number>();
  for (const [index, rawLine] of text.split(/\r?\n/).entries()) {
    const line = rawLine.trim();
    if (!line) continue;
    const parts = line.split(/[\t,; ]+/).filter(Boolean);
    if (parts.length < 2) {
      throw new Error(`Dòng ${index + 1}: cần mã và số lượng`);
    }
    const symbol = parts[0].trim().toUpperCase();
    if (["MÃ", "MA", "SYMBOL", "TICKER"].includes(symbol)) continue;
    const shares = Number(parts[1].replace(/[.,]/g, ""));
    if (!/^[A-Z0-9]+$/.test(symbol)) {
      throw new Error(`Dòng ${index + 1}: mã không hợp lệ`);
    }
    if (!Number.isSafeInteger(shares) || shares < 0) {
      throw new Error(`Dòng ${index + 1}: số lượng phải là số nguyên không âm`);
    }
    combined.set(symbol, (combined.get(symbol) ?? 0) + shares);
  }
  return [...combined.entries()]
    .filter(([, shares]) => shares > 0)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([symbol, shares]) => ({ symbol, shares }));
}

export function holdingsToText(holdings: FundHolding[]): string {
  return holdings.map((item) => `${item.symbol}\t${item.shares}`).join("\n");
}

interface Props {
  value: string;
  onChange: (value: string) => void;
  updatedAt: string | null;
  saving: boolean;
  onSave: () => void;
  onClear: () => void;
}

export function HoldingsEditor({
  value,
  onChange,
  updatedAt,
  saving,
  onSave,
  onClear,
}: Props) {
  return (
    <details className="rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-900">
      <summary className="cursor-pointer px-5 py-4 font-semibold hover:text-blue-600 dark:hover:text-blue-400">
        Danh mục hiện tại <span className="font-normal text-slate-400">— không bắt buộc</span>
      </summary>
      <div className="border-t border-slate-100 px-5 pb-5 pt-4 dark:border-slate-800">
        <p className="mb-3 text-sm text-slate-500 dark:text-slate-400">
          Dán hai cột từ Excel hoặc nhập mỗi dòng theo dạng <b>MÃ SỐ_LƯỢNG</b>.
          Hệ thống chỉ ghi nhớ khi bạn bấm “Lưu danh mục”.
        </p>
        <textarea
          value={value}
          onChange={(event) => onChange(event.target.value)}
          rows={7}
          spellCheck={false}
          placeholder={"FPT\t1200\nVCB\t500\nHPG\t2000"}
          className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 font-mono text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 dark:border-slate-700 dark:bg-slate-950 dark:focus:ring-blue-900/30"
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={onSave}
            disabled={saving}
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900"
          >
            {saving ? "Đang lưu..." : "Lưu danh mục"}
          </button>
          <button
            type="button"
            onClick={onClear}
            className="rounded-lg px-3 py-2 text-sm text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
          >
            Xóa ô nhập
          </button>
          <span className="text-xs text-slate-400">
            {updatedAt
              ? `Đã lưu: ${new Date(updatedAt).toLocaleString("vi-VN")}`
              : "Chưa có danh mục đã lưu"}
          </span>
        </div>
      </div>
    </details>
  );
}
