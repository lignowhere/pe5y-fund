const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8002";

async function fetchJson<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const url = new URL(path, API_BASE);
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, String(v)));
  }
  const res = await fetch(url.toString());
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

export interface HealthStatus {
  status: string;
  db_path: string;
  db_exists: boolean;
}

export interface DataStatus {
  price_latest_date: string;
  price_symbol_count: number;
  ratio_latest_year: number;
  ratio_symbol_count: number;
  missing_price_count: number;
}

export interface FundPreference {
  strategy: "TTM_20Q" | "LAST_8Q_PLUS";
  select_pct: 10 | 12 | 14 | 16;
  updated_at: string | null;
}

export interface FundHolding {
  symbol: string;
  shares: number;
}

export interface FundHoldingsResponse {
  holdings: FundHolding[];
  updated_at: string | null;
}

export interface FundPlanPosition {
  symbol: string;
  signal_rank: number | null;
  source: "PRIMARY" | "CURRENT_ONLY";
  rebalance_price_vnd: number | null;
  rebalance_price_date: string | null;
  adjusted_rebalance_price_vnd: number | null;
  current_price_vnd: number;
  price_date: string | null;
  price_return_pct: number | null;
  corporate_action_share_factor?: number | null;
  cash_dividend_vnd_per_initial_share?: number | null;
  corporate_action_count?: number | null;
  initial_weight_pct: number;
  drift_weight_pct: number;
  target_weight_pct: number;
  desired_shares: number;
  target_shares: number;
  target_value_vnd: number;
  adv_shares: number | null;
  capacity_shares: number | null;
  liquidity_limited: boolean;
  current_shares: number;
  delta_shares: number;
  action: "MUA" | "BÁN" | "GIỮ";
  trade_value_vnd: number;
}

export interface FundPlanResult {
  nav_vnd: number;
  strategy: string;
  select_pct: number;
  has_current_holdings: boolean;
  rebalance_date: string;
  signal_cutoff: string | null;
  signal_price_date: string | null;
  execution_date: string | null;
  snapshot_id: number | null;
  snapshot_set_id: number | null;
  snapshot_created_at: string | null;
  financial_data_version_id: number | null;
  financial_content_hash: string | null;
  methodology_version: string | null;
  universe_count: number | null;
  price_date: string | null;
  price_basis: "strategy_date_drift";
  performance_basis:
    | "verified_corporate_action_ledger_v1"
    | "vendor_adjusted_total_return_research"
    | "vendor_adjusted_total_return_user_confirmed";
  trust_tier: "strict_pit" | "legacy_research" | "trusted_local";
  performance_source_as_of: string | null;
  model_growth_multiple: number;
  benchmark: {
    symbol: string;
    rebalance_date: string;
    rebalance_value: number;
    current_date: string;
    current_value: number;
    growth_multiple: number;
    performance_basis:
      | "verified_total_return_index"
      | "vendor_adjusted_total_return_research"
      | "vendor_adjusted_total_return_user_confirmed"
      | "vendor_adjusted_comparison"
      | "unadjusted_price_index";
    authoritative: boolean;
  } | null;
  summary: {
    strategy_price_return_pct: number;
    strategy_total_return_pct: number;
    model_cash_weight_pct: number;
    model_cash_vnd: number;
    model_value_per_100m_vnd: number;
    benchmark_symbol: string;
    benchmark_return_pct: number | null;
    benchmark_value_per_100m_vnd: number | null;
    excess_return_pct: number | null;
    gainers_count: number;
    losers_count: number;
    unchanged_count: number;
    target_stock_count: number;
    target_deployed_vnd: number;
    target_cash_vnd: number;
    liquidity_limited_count: number;
    current_holdings_value_vnd: number;
    implied_cash_vnd: number;
    estimated_buy_vnd: number;
    estimated_sell_vnd: number;
  };
  positions: FundPlanPosition[];
  warnings: Array<{ code: string; message: string }>;
}

export interface SyncStatus {
  running: boolean;
  needs_sync: boolean;
  legacy_research_planner_enabled?: boolean;
  trusted_local_planner_enabled?: boolean;
  prices_need_sync?: boolean;
  financials_need_sync?: boolean;
  snapshots_need_sync?: boolean;
  broad_price_date: string | null;
  latest_market_date: string | null;
  completed_market_session?: string | null;
  provisional_prices?: {
    rows: number;
    symbols: number;
    latest: string | null;
  };
  fallback_available?: boolean;
  fallback_mode?: "comparison_only";
  source_health?: Array<{
    source: string;
    capability: string;
    available: number;
    last_status_code: number | null;
    last_error: string | null;
    checked_at: string;
  }>;
  config_state?: {
    status: "active" | "pending";
    pending: {
      id: number;
      config_hash: string;
      created_at: string;
    } | null;
  };
  financial_version?: {
    id: number;
    content_hash: string;
    created_at: string;
    point_in_time_ready?: number;
    official_provenance_ready?: number;
    quality_status?: string;
    quality_issues_json?: string;
    publication_coverage_pct?: number;
    methodology_version?: string;
  } | null;
  strategy_snapshot?: {
    snapshot_set_id: number;
    financial_data_version_id: number;
    config_hash: string;
    financial_content_hash: string;
    methodology_version?: string;
    publication_coverage_pct?: number;
    pit_policy?: string;
    price_basis?: string;
    execution_price_basis?: string;
    signal_price_basis?: string;
    lifecycle_status?: "building" | "active" | "quarantined" | "failed";
    portfolio_ready?: number;
    performance_ready?: number;
    backtest_ready?: number;
    investment_ready?: boolean;
    user_confirmed_ready?: boolean;
    trusted_local_ready?: number;
    trusted_local_attestation_id?: number | null;
    trusted_local_attestation_hash?: string | null;
    trusted_local_attested_at?: string | null;
    research_planner_available?: boolean;
    research_planner_cycles?: Array<{
      strategy: string;
      select_pct: number;
      hold_year: number;
      selected_count: number;
    }>;
    blocking_issues?: string[];
    signal_cutoff?: string | null;
    signal_price_date?: string | null;
    execution_date?: string | null;
    current_verified_price_date?: string | null;
    universe_coverage?: {
      eligible: number | null;
      selected: number | null;
    };
    validation_status?:
      | "verified"
      | "portfolio_ready"
      | "user_confirmed_local"
      | "quarantined"
      | "blocked";
    valid_cycle_count?: number;
    research_cycle_count?: number;
    strict_coverage?: {
      first_hold_year: number | null;
      last_hold_year: number | null;
    };
    activated_at: string | null;
    backtests?: Array<{
      strategy: string;
      select_pct: number;
      pit_tier: "strict_pit" | "legacy_research" | "trusted_local";
      start_hold_year: number;
      end_hold_year: number;
      cycle_count: number;
      capital_vnd: number;
      net_cagr: number;
      win_rate: number;
      price_basis: string;
      benchmark_symbol: string;
      benchmark_cagr: number | null;
      authoritative: boolean;
      user_confirmed?: boolean;
      excluded_cycles: Array<{ hold_year: number; reason: string }>;
    }>;
  } | null;
  last_run: {
    status: string;
    stage: string;
    started_at: string;
    finished_at: string | null;
    prices_updated?: number;
    prices_failed?: number;
    price_symbols_total?: number;
    prices_processed?: number;
    financials_updated?: number;
    financials_failed?: number;
    financial_symbols_total?: number;
    financial_rows_staged?: number;
    financial_version_id?: number | null;
    snapshot_set_id?: number | null;
    message?: string | null;
  } | null;
}

export interface VerifyComparison {
  metric: string;
  vci: string | null;
  kbs: string | null;
  diff_pct: number | null;
  status: string;
  note: string;
}

export interface VerifyResult {
  symbol: string;
  year: number;
  overall_status: string;
  ok: number;
  warnings: number;
  errors: number;
  comparisons: VerifyComparison[];
}

export interface DbHealthPrice {
  symbols_covered: number;
  total_rows: number;
  earliest_date: string | null;
  latest_date: string | null;
  missing_count: number;
  missing_symbols: string[];
  behind_count: number;
  behind_symbols: string[];
  stale_count: number;
  stale_symbols: string[];
  coverage_pct: number;
  historical_coverage_pct?: number;
  completed_session?: string | null;
  completed_session_symbols?: number;
  provisional?: {
    rows: number;
    symbols: number;
    latest: string | null;
  };
}

export interface DbHealthFinancials {
  symbols_covered: number;
  total_rows: number;
  earliest_year: number | null;
  latest_year: number | null;
  missing_count: number;
  missing_symbols: string[];
  check_year: number;
  coverage_pct: number;
}

export interface ExchangeBreakdown {
  exchange: string;
  total: number;
  with_price: number;
}

export interface DbHealth {
  total_symbols: number;
  price: DbHealthPrice;
  financials: DbHealthFinancials;
  exchanges: ExchangeBreakdown[];
  fallback_available?: boolean;
  source_health?: SyncStatus["source_health"];
  refreshed_at?: string;
  cached?: boolean;
}

export interface UpdateResult {
  message: string;
  result: {
    updated: number;
    failed: number;
    inserted: number;
    errors: string[];
  } | null;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const url = new URL(path, API_BASE);
  const res = await fetch(url.toString(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : "{}",
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

async function deleteJson<T>(path: string): Promise<T> {
  const url = new URL(path, API_BASE);
  const res = await fetch(url.toString(), { method: "DELETE" });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  if (res.status === 204) return undefined as T;
  return res.json();
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const url = new URL(path, API_BASE);
  const res = await fetch(url.toString(), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

export interface StrategyConfig {
  rebalance_month: number;
  select_pcts: number[];
  min_holdings: number;
  participation_rate: number;
  accum_days: number;
  lot_size: number;
  mcap_base_vnd: number;
  mcap_growth_rate: number;
  mcap_growth_period_years: number;
  mcap_base_year: number;
  min_trading_days: number;
  min_avg_dollar_volume_vnd: number;
  max_zero_volume_frac: number;
  max_stale_close_frac: number;
  max_rebalance_gap_days: number;
  broker_fee_bps: number;
  sell_tax_bps: number;
  benchmark_symbol: string;
  _config_status?: "active" | "pending";
  _pending_config?: {
    id: number;
    status?: string;
    config: StrategyConfig;
    config_hash: string;
    created_at?: string;
  } | null;
}

export interface StreamProgress {
  type: "start" | "progress" | "done";
  symbol?: string;
  index?: number;
  total?: number;
  status?: string;
  bars?: number;         // price update
  rows?: number;         // financials update
  source?: string;       // "VCI" or "KBS"
  error?: string | null;
  skip_reason?: string | null;
  updated?: number;
  failed?: number;
  inserted?: number;
  symbols?: string[];
  remaining_missing?: number;
}

/** Shared SSE stream reader with auto-reconnect on network errors. */
function _streamSSE(
  url: string,
  onEvent: (ev: StreamProgress) => void,
  onDone: () => void,
  maxRetries = 2,
): AbortController {
  const ctrl = new AbortController();
  let retries = 0;

  function attempt() {
    fetch(url, { signal: ctrl.signal })
      .then(async (res) => {
        if (!res.ok || !res.body) {
          onEvent({ type: "done", updated: 0, failed: 0, inserted: 0 });
          onDone();
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith("data: ")) {
              try {
                onEvent(JSON.parse(trimmed.slice(6)) as StreamProgress);
              } catch { /* skip malformed */ }
            }
          }
        }
        retries = 0;
        onDone();
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (retries < maxRetries) {
          retries++;
          const delay = Math.min(1000 * 2 ** retries, 5000);
          console.warn(`SSE connection lost, retry ${retries}/${maxRetries} in ${delay}ms`);
          setTimeout(attempt, delay);
        } else {
          onEvent({ type: "done", updated: 0, failed: -1, inserted: 0 });
          onDone();
        }
      });
  }

  attempt();
  return ctrl;
}

function streamPriceUpdate(
  onEvent: (ev: StreamProgress) => void,
  onDone: () => void,
): AbortController {
  return _streamSSE(
    new URL("/api/data/update/prices/stream", API_BASE).toString(),
    onEvent, onDone,
  );
}

function streamFinancialsUpdate(
  onEvent: (ev: StreamProgress) => void,
  onDone: () => void,
  year?: number,
): AbortController {
  const url = new URL("/api/data/update/financials/stream", API_BASE);
  if (year) url.searchParams.set("year", String(year));
  return _streamSSE(url.toString(), onEvent, onDone);
}

// API functions
export const api = {
  health: () => fetchJson<HealthStatus>("/api/health"),
  dataStatus: () => fetchJson<DataStatus>("/api/data/status"),
  syncStatus: () => fetchJson<SyncStatus>("/api/data/sync/status"),
  startSync: () => postJson<{ started: boolean } & SyncStatus>("/api/data/sync/start"),
  cancelSync: () => postJson<{ cancel_requested: boolean }>("/api/data/sync/cancel"),
  dbHealth: () => fetchJson<DbHealth>("/api/data/health"),
  search: (q: string, limit = 20) =>
    fetchJson<Array<{ ticker: string; organ_name: string }>>("/api/data/search", { q, limit }),

  verify: (symbol: string, year?: number) =>
    fetchJson<VerifyResult>(`/api/verify/${symbol}`, year ? { year } : {}),

  triggerPriceUpdate: (symbols?: string[]) =>
    postJson<UpdateResult>("/api/data/update/prices", { symbols: symbols ?? null, count_back: 30 }),

  strategyConfig: () => fetchJson<StrategyConfig>("/api/strategy/config"),
  strategyDefaults: () => fetchJson<StrategyConfig>("/api/strategy/config/defaults"),
  saveStrategyConfig: (data: StrategyConfig) => {
    const { _config_status, _pending_config, ...payload } = data;
    void _config_status;
    void _pending_config;
    return putJson<StrategyConfig>("/api/strategy/config", payload);
  },
  resetStrategyConfig: () =>
    postJson<StrategyConfig>("/api/strategy/config/reset"),

  fundPreferences: () => fetchJson<FundPreference>("/api/fund/preferences"),
  saveFundPreferences: (data: Pick<FundPreference, "strategy" | "select_pct">) =>
    putJson<FundPreference>("/api/fund/preferences", data),
  fundHoldings: () => fetchJson<FundHoldingsResponse>("/api/fund/holdings"),
  saveFundHoldings: (holdings: FundHolding[]) =>
    putJson<FundHoldingsResponse>("/api/fund/holdings", { holdings }),
  deleteFundHoldings: () => deleteJson<void>("/api/fund/holdings"),
  portfolioPlan: (data: {
    nav_vnd: number;
    strategy: FundPreference["strategy"];
    select_pct: FundPreference["select_pct"];
    holdings?: FundHolding[];
    auto_sync?: boolean;
  }) => postJson<FundPlanResult>("/api/fund/portfolio-plan", data),

  streamPriceUpdate,
  streamFinancialsUpdate,
};
