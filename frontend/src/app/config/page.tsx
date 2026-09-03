"use client";

import { useEffect, useState } from "react";
import { api, StrategyConfig } from "@/lib/api";

/* ---------- field metadata ---------- */
interface Field {
  key: keyof StrategyConfig;
  label: string;
  desc: string;
  unit?: string;
  scale?: number;   // multiply stored value for display (e.g. 0.10 → 10%)
  min?: number;
  max?: number;
  step?: number;
  type?: "array" | "text";   // special handling for select_pcts / text fields
}

const SECTIONS: { title: string; fields: Field[] }[] = [
  {
    title: "Quy mô danh mục",
    fields: [
      {
        key: "min_holdings",
        label: "Số mã tối thiểu",
        desc: "Danh mục luôn chọn ít nhất 15 mã khi vũ trụ đủ lớn",
        unit: "mã",
        min: 15,
        max: 100,
        step: 1,
      },
    ],
  },
  {
    title: "Tái cân bằng & Lọc",
    fields: [
      { key: "rebalance_month", label: "Tháng tái cân bằng", desc: "Tháng thực hiện rebalance hàng năm", min: 1, max: 12, step: 1 },
      { key: "select_pcts", label: "Tỷ lệ chọn (%)", desc: "Danh sách % chọn cổ phiếu, cách nhau bởi dấu phẩy", type: "array" },
    ],
  },
  {
    title: "Bộ lọc vốn hóa",
    fields: [
      { key: "mcap_base_vnd", label: "Vốn hóa tối thiểu", desc: "Ngưỡng vốn hóa gốc (năm base)", unit: "tỷ VND", scale: 1e-9, min: 0, step: 10 },
      { key: "mcap_growth_rate", label: "Tốc độ tăng", desc: "% tăng vốn hóa tối thiểu mỗi chu kỳ", unit: "%", scale: 100, min: 0, max: 100, step: 1 },
      { key: "mcap_growth_period_years", label: "Chu kỳ tăng", desc: "Số năm cho mỗi lần tăng ngưỡng", unit: "năm", min: 1, max: 10, step: 1 },
      { key: "mcap_base_year", label: "Năm gốc", desc: "Năm bắt đầu áp dụng ngưỡng vốn hóa", min: 2000, max: 2030, step: 1 },
    ],
  },
  {
    title: "Bộ lọc thanh khoản",
    fields: [
      { key: "min_trading_days", label: "Ngày GD tối thiểu", desc: "Số ngày giao dịch tối thiểu trong năm", unit: "ngày", min: 1, max: 365, step: 1 },
      { key: "min_avg_dollar_volume_vnd", label: "GTGD bình quân tối thiểu", desc: "Giá trị giao dịch bình quân ngày tối thiểu", unit: "triệu VND", scale: 1e-6, min: 0, step: 10 },
      { key: "max_zero_volume_frac", label: "% ngày volume = 0", desc: "Tỷ lệ tối đa ngày không có giao dịch", unit: "%", scale: 100, min: 0, max: 100, step: 0.5 },
      { key: "max_stale_close_frac", label: "% giá không đổi", desc: "Tỷ lệ tối đa ngày giá đóng cửa không thay đổi", unit: "%", scale: 100, min: 0, max: 100, step: 1 },
    ],
  },
  {
    title: "Sizing vị thế",
    fields: [
      { key: "participation_rate", label: "Tỷ lệ tham gia KLGD", desc: "% khối lượng giao dịch bình quân mỗi ngày", unit: "%", scale: 100, min: 1, max: 100, step: 1 },
      { key: "accum_days", label: "Ngày tích lũy", desc: "Số ngày mua gom cổ phiếu", unit: "ngày", min: 1, max: 100, step: 1 },
      { key: "lot_size", label: "Lô chuẩn", desc: "Số cổ phiếu tối thiểu mỗi lô", unit: "CP", min: 1, max: 1000, step: 100 },
      { key: "max_rebalance_gap_days", label: "Gap rebalance tối đa", desc: "Số ngày tối đa tìm giá khi rebalance", unit: "ngày", min: 1, max: 30, step: 1 },
    ],
  },
  {
    title: "Chi phí giao dịch",
    fields: [
      { key: "broker_fee_bps", label: "Phí môi giới mỗi chiều", desc: "Phí áp dụng riêng cho lệnh mua và bán", unit: "bps", min: 0, max: 100, step: 1 },
      { key: "sell_tax_bps", label: "Thuế bán", desc: "Thuế chỉ áp dụng khi bán", unit: "bps", min: 0, max: 100, step: 1 },
    ],
  },
  {
    title: "Benchmark",
    fields: [
      { key: "benchmark_symbol", label: "Mã benchmark", desc: "Mã chỉ số dùng so sánh hiệu suất (VD: VNINDEX, VN30)", type: "text" },
    ],
  },
];

/* ---------- helpers ---------- */
const CARD = "bg-white dark:bg-gray-900 rounded-xl shadow-sm dark:shadow-gray-900/20 border border-gray-200 dark:border-gray-700 p-5";
const INPUT = "w-full px-3 py-2 border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 dark:text-gray-100 focus:ring-2 focus:ring-blue-500 focus:outline-none text-sm";

function toDisplay(val: number, scale?: number): string {
  return scale ? String(+(val * scale).toPrecision(10)) : String(val);
}
function fromDisplay(str: string, scale?: number): number {
  const n = parseFloat(str);
  return scale ? n / scale : n;
}

/* ---------- component ---------- */
export default function ConfigPage() {
  const [config, setConfig] = useState<StrategyConfig | null>(null);
  const [defaults, setDefaults] = useState<StrategyConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const [configStatus, setConfigStatus] = useState<"active" | "pending">("active");

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.strategyConfig(), api.strategyDefaults()])
      .then(([cfg, def]) => {
        if (!cancelled) {
          setConfig(cfg._pending_config?.config ?? cfg);
          setConfigStatus(cfg._config_status ?? "active");
          setDefaults(def);
        }
      })
      .catch(() => { if (!cancelled) setMsg({ text: "Không tải được cấu hình", ok: false }); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  function updateField(key: keyof StrategyConfig, raw: string, field: Field) {
    if (!config) return;
    if (field.type === "array") {
      const arr = raw.split(",").map((s) => parseFloat(s.trim())).filter((n) => !isNaN(n));
      setConfig({ ...config, [key]: arr });
    } else if (field.type === "text") {
      setConfig({ ...config, [key]: raw });
    } else {
      const val = fromDisplay(raw, field.scale);
      if (!isNaN(val)) setConfig({ ...config, [key]: val });
    }
  }

  async function handleSave() {
    if (!config) return;
    setSaving(true); setMsg(null);
    try {
      const res = await api.saveStrategyConfig(config);
      setConfig(res._pending_config?.config ?? res);
      setConfigStatus(res._config_status ?? "pending");
      setMsg({ text: "Đã lưu bản chờ; chỉ kích hoạt sau khi snapshot mới hoàn tất.", ok: true });
    } catch (e: unknown) {
      setMsg({ text: e instanceof Error ? e.message : "Lỗi lưu cấu hình", ok: false });
    } finally { setSaving(false); }
  }

  async function handleReset() {
    setSaving(true); setMsg(null);
    try {
      const res = await api.resetStrategyConfig();
      setConfig(res._pending_config?.config ?? res);
      setConfigStatus(res._config_status ?? "pending");
      setMsg({ text: "Đã tạo bản chờ với cấu hình mặc định", ok: true });
    } catch (e: unknown) {
      setMsg({ text: e instanceof Error ? e.message : "Lỗi reset", ok: false });
    } finally { setSaving(false); }
  }

  if (loading) return <div className="text-center py-12 text-gray-500">Đang tải cấu hình...</div>;
  if (!config) return <div className="text-center py-12 text-red-500">Không tải được cấu hình</div>;

  const isModified = defaults && JSON.stringify(config) !== JSON.stringify(defaults);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className={CARD}>
        <h1 className="text-2xl font-bold">Cấu hình chiến lược PE_TTM_20Q</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Thay đổi được lưu dưới dạng bản chờ và chỉ có hiệu lực sau khi backtest, snapshot mới hoàn tất.
        </p>
        {configStatus === "pending" && (
          <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm font-medium text-amber-700 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
            Cấu hình đang chờ dựng snapshot; planner vẫn dùng cấu hình active gần nhất.
          </p>
        )}
      </div>

      {/* Sections */}
      {SECTIONS.map((sec) => (
        <div key={sec.title} className={CARD}>
          <h2 className="text-lg font-semibold mb-4">{sec.title}</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {sec.fields.map((f) => {
              const val = config[f.key];
              const defVal = defaults?.[f.key];
              const display = f.type === "array"
                ? (val as number[]).join(", ")
                : f.type === "text"
                  ? String(val)
                  : toDisplay(val as number, f.scale);
              const isChanged = defVal !== undefined && JSON.stringify(val) !== JSON.stringify(defVal);

              return (
                <div key={f.key}>
                  <label htmlFor={`strategy-config-${String(f.key)}`} className="text-sm font-medium text-gray-700 dark:text-gray-300 flex items-center gap-2">
                    {f.label}
                    {f.unit && <span className="text-xs text-gray-400 font-normal">({f.unit})</span>}
                    {isChanged && <span className="w-1.5 h-1.5 rounded-full bg-blue-500 inline-block" title="Đã thay đổi" />}
                  </label>
                  <p className="text-xs text-gray-400 dark:text-gray-500 mb-1">{f.desc}</p>
                  <input
                    id={`strategy-config-${String(f.key)}`}
                    type={f.type === "array" || f.type === "text" ? "text" : "number"}
                    value={display}
                    onChange={(e) => updateField(f.key, e.target.value, f)}
                    className={INPUT}
                    min={f.min} max={f.max} step={f.step}
                  />
                </div>
              );
            })}
          </div>
        </div>
      ))}

      {/* Actions */}
      <div className="flex flex-wrap items-center gap-3">
        <button onClick={handleSave} disabled={saving}
          className="px-6 py-2.5 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors">
          {saving ? "Đang lưu..." : "Lưu cấu hình"}
        </button>
        <button onClick={handleReset} disabled={saving}
          className="px-6 py-2.5 bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 rounded-lg font-medium hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-50 transition-colors">
          Khôi phục mặc định
        </button>
        {isModified && (
          <span className="text-xs text-blue-500 dark:text-blue-400">Có thay đổi so với mặc định</span>
        )}
        {msg && (
          <span className={`text-sm font-medium ${msg.ok ? "text-green-600 dark:text-green-400" : "text-red-500 dark:text-red-400"}`}>
            {msg.text}
          </span>
        )}
      </div>
    </div>
  );
}
