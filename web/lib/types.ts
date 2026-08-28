import type { ComponentType } from "react";
import type { RiskBudgetReport } from "@/lib/correlationRiskBanner";

export type MessageRole = "assistant" | "user";

/** Visual signature any nav glyph must satisfy. Covers both the legacy
 *  lucide-react components and the Radon glyph set defined in
 *  components/icons/RadonGlyphs.tsx. */
export type NavIcon = ComponentType<{
  size?: number;
  color?: string;
  strokeWidth?: number;
}>;

export type Message = {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
};

export type FlowRow = {
  ticker: string;
  position: string;
  flowLabel: string;
  flowClass: string;
  strength: string;
  note: string;
};

export type ApiMessage = {
  role: MessageRole;
  content: string;
};

export type AssistantToolEvent = {
  name: string;
  input: Record<string, unknown>;
  ok: boolean;
  error?: string;
  /** Loop de-duplicated an identical call and replayed the cached result. */
  repeated?: boolean;
};

export type AssistantOrderProposal = {
  tool: string;
  destructive: true;
  input: AssistantOrderInput;
  summary: string;
  toolUseId: string;
};

export type AssistantOrderComboLeg = {
  expiry: string;
  strike: number;
  right: "C" | "P";
  action: "BUY" | "SELL";
  ratio: number;
};

export type AssistantOrderInput =
  | {
      type: "stock";
      ticker: string;
      action: "BUY" | "SELL";
      quantity: number;
      limit_price: number;
    }
  | {
      type: "option";
      ticker: string;
      action: "BUY" | "SELL";
      quantity: number;
      limit_price: number;
      expiry: string;
      strike: number;
      right: "C" | "P";
      conId: number;
      exchange: string;
    }
  | {
      type: "combo";
      ticker: string;
      action: "BUY" | "SELL";
      quantity: number;
      limit_price: number;
      structure?: string;
      legs: AssistantOrderComboLeg[];
    };

export type AssistantResponse = {
  content?: string;
  model?: string;
  error?: string;
  toolEvents?: AssistantToolEvent[];
  proposal?: AssistantOrderProposal | null;
  rounds?: number;
};

export type PiResponse = {
  command: string;
  status: "ok" | "error";
  output: string;
  stderr?: string;
  error?: string;
};

export type WorkspaceSection = "dashboard" | "flow-analysis" | "options" | "portfolio" | "performance" | "orders" | "scanner" | "discover" | "watchlist" | "journal" | "regime" | "cta" | "alerts" | "workflow" | "ticker-detail" | "admin" | "preferences" | "profile";

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export type NavGroupId = "overview" | "positions" | "research" | "risk" | "operations";

export type WorkspaceNavItem = {
  label: string;
  route: WorkspaceSection;
  href: string;
  icon: NavIcon;
  hidden?: boolean;
  group: NavGroupId;
};

export type PortfolioLeg = {
  con_id?: number | null;
  /** Per-leg expiry for calendars/diagonals. Falls back to position expiry. */
  expiry?: string | null;
  direction: "LONG" | "SHORT";
  contracts: number;
  type: "Call" | "Put" | "Stock";
  strike: number | null;
  entry_cost: number;
  avg_cost: number;
  market_price: number | null;
  market_value: number | null;
  market_price_is_calculated?: boolean;
};

export type LegacyPositionReturnCapitalPayload = {
  amount: number | null;
  kind: "opening-margin";
  source: string | null;
  as_of: string | null;
  quality: "exact" | "fill-linked" | "estimated";
  fill_linked: boolean;
};

export type PositionReturnCapitalPayloadV2 = {
  version: 2;
  amount: number;
  currency: string;
  measurement:
    | {
        quality: "exact";
        method: "payoff-max-loss" | "debit-paid";
        measured_at: string;
      }
    | {
        quality: "observed";
        method: "isolated-account-margin-delta";
        measured_at: string;
        observation_id: string;
        isolation: "isolated";
        before_sample_id: string;
        after_sample_id: string;
        window_seconds: number;
        concurrent_exec_ids: string[];
      }
    | {
        quality: "estimated";
        method: "ib-whatif" | "reg-t-model";
        measured_at: string;
      };
  linkage:
    | {
        state: "linked";
        account_id: string;
        position_instance_id: string;
        con_ids: number[];
        order_refs: string[];
        perm_ids: number[];
        exec_ids: string[];
        legs: Array<{ con_id: number; currency: string; multiplier: number }>;
      }
    | { state: "unlinked"; reason: string };
};

/** Legacy is retained for safe parsing only; the Return % resolver rejects it. */
export type PositionReturnCapitalPayload =
  | PositionReturnCapitalPayloadV2
  | LegacyPositionReturnCapitalPayload;

export type LegacyEntryMarginMetadata = Omit<LegacyPositionReturnCapitalPayload, "amount">;

export type PortfolioPosition = {
  id: number;
  account_id?: string | null;
  position_instance_id?: string | null;
  ticker: string;
  structure: string;
  structure_type: string;
  risk_profile: string;
  expiry: string;
  contracts: number;
  direction: string;
  entry_cost: number;
  max_risk: number | null;
  /**
   * Legacy projected margin field. It is not a valid return denominator unless
   * `init_margin_at_entry_metadata` proves the value is fill-linked.
   */
  init_margin_at_entry?: number | null;
  init_margin_at_entry_metadata?: LegacyEntryMarginMetadata | null;
  /** Future-facing, fill-linked capital basis from the position ledger. */
  return_capital?: PositionReturnCapitalPayload | null;
  market_value: number | null;
  legs: PortfolioLeg[];
  market_price_is_calculated?: boolean;
  /** IB's per-position daily P&L from reqPnLSingle.
   *  Correctly handles intraday additions (only overnight contracts use
   *  yesterday's close; today's adds use fill price as reference).
   *  Preferred over WS close-based calculation. */
  ib_daily_pnl?: number | null;
  kelly_optimal: number | null;
  target: number | null;
  stop: number | null;
  entry_date: string;
};

export type OrderComboLeg = {
  conId: number;
  ratio: number;
  action: string;
  symbol?: string;
  strike?: number | null;
  right?: string | null;
  expiry?: string | null;
};

export type OrderContract = {
  conId: number | null;
  symbol: string;
  secType: string;
  strike: number | null;
  right: string | null;
  expiry: string | null;
  comboLegs?: OrderComboLeg[];
};

export type OpenOrder = {
  orderId: number;
  permId: number;
  symbol: string;
  contract: OrderContract;
  action: string;
  orderType: string;
  totalQuantity: number;
  limitPrice: number | null;
  auxPrice: number | null;
  status: string;
  filled: number;
  remaining: number;
  avgFillPrice: number | null;
  tif: string;
  /** IB outsideRth. Missing/undefined is false (IB default). */
  outsideRth?: boolean;
  orderRef?: string | null;
  ocaGroup?: string | null;
  parentId?: number | null;
};

export type ExecutedOrder = {
  execId: string;
  account_id?: string | null;
  permId?: number | null;
  orderId?: number | null;
  clientId?: number | null;
  orderRef?: string | null;
  symbol: string;
  contract: OrderContract;
  side: string;
  quantity: number;
  avgPrice: number | null;
  commission: number | null;
  realizedPNL: number | null;
  /** IB's commission-report figure when `realizedPNL` was replaced by the
   *  journal average-cost value (`realizedPNLSource === "journal"`). */
  ibRealizedPNL?: number | null;
  realizedPNLSource?: "journal" | "ib";
  time: string;
  exchange: string;
};

export type OrdersData = {
  last_sync: string;
  open_orders: OpenOrder[];
  executed_orders: ExecutedOrder[];
  open_count: number;
  executed_count: number;
};

export type AccountSummary = {
  net_liquidation: number;
  daily_pnl: number | null;
  unrealized_pnl: number;
  realized_pnl: number;
  settled_cash: number;
  maintenance_margin: number;
  excess_liquidity: number;
  buying_power: number;
  dividends: number;
  /** TotalCashValue — total cash including unsettled proceeds */
  cash?: number;
  /** InitMarginReq — initial margin requirement */
  initial_margin?: number;
  /** AvailableFunds — EWL minus initial margin */
  available_funds?: number;
  /** EquityWithLoanValue — equity including loan value */
  equity_with_loan?: number;
  /** PreviousDayEquityWithLoanValue */
  previous_day_ewl?: number;
  /** RegTEquity — Regulation T equity */
  reg_t_equity?: number;
  /** SMA — Special Memorandum Account */
  sma?: number;
  /** GrossPositionValue — securities gross position value */
  gross_position_value?: number;
};

export type PortfolioData = {
  bankroll: number;
  peak_value: number;
  last_sync: string;
  positions: PortfolioPosition[];
  total_deployed_pct: number;
  total_deployed_dollars: number;
  remaining_capacity_pct: number;
  position_count: number;
  defined_risk_count: number;
  undefined_risk_count: number;
  avg_kelly_optimal: number | null;
  /** Gate-3 correlated-exposure report computed with each canonical snapshot. */
  risk_budget?: RiskBudgetReport | null;
  account_summary?: AccountSummary;
  /** Ticker -> LATEST journal trade date. Coarse entry-time fallback for share
   *  cards; only usable when it predates the exit. */
  trade_log_dates?: Record<string, string>;
  /** `SYMBOL|EXPIRY|RIGHT|STRIKE` -> earliest opening-fill date. Per-contract
   *  entry day for share-card hold time, correct even after a position is fully
   *  closed and gone from the portfolio. */
  contract_open_dates?: Record<string, string>;
};

/** Serializable server-to-client seed for the portfolio hook. */
export type PortfolioSnapshotSeed = {
  data: PortfolioData;
  warning: string | null;
};

export type PerformanceSeriesPoint = {
  date: string;
  equity: number;
  daily_return: number | null;
  drawdown: number;
  benchmark_close?: number;
  benchmark_return?: number;
  /** v2 only: dollar NAV and the base-100 TWR index for the same session. */
  nav?: number;
  twr_index?: number;
  cum_return?: number;
  flow?: number;
  skipped?: boolean;
};

export type PerformanceSummary = {
  starting_equity?: number;
  ending_equity: number;
  pnl: number;
  trading_days: number;
  total_return: number;
  annualized_return?: number;
  annualized_volatility?: number;
  downside_deviation?: number;
  sharpe_ratio?: number;
  sortino_ratio?: number;
  calmar_ratio?: number;
  max_drawdown?: number;
  current_drawdown?: number;
  max_drawdown_duration_days?: number;
  beta?: number;
  alpha?: number;
  correlation?: number;
  r_squared?: number;
  tracking_error?: number;
  information_ratio?: number;
  treynor_ratio?: number;
  upside_capture?: number;
  downside_capture?: number;
  var_95?: number;
  cvar_95?: number;
  tail_ratio?: number;
  ulcer_index?: number;
  skew?: number;
  kurtosis?: number;
  hit_rate?: number;
  positive_days?: number;
  negative_days?: number;
  flat_days?: number;
  best_day?: number;
  worst_day?: number;
  average_up_day?: number;
  average_down_day?: number;
  win_loss_ratio?: number;
};

/** Structured warning (payload v2). §C.4 — the UI dispatches on `code`. */
export type PerformanceWarning = {
  code: string;
  severity: "info" | "warn" | "error";
  message: string;
  context?: Record<string, unknown>;
};

/**
 * A complete benchmark block (payload v2 §A.4.1). There is no partially
 * populated block: either every field is present or the block is null and no
 * benchmark-derived number appears anywhere.
 */
export type PerformanceBenchmarkBlock = {
  symbol: string;
  n_common: number;
  coverage: number;
  benchmark_return: number;
  beta: number;
  alpha_annualized: number;
  correlation: number;
  r_squared: number;
  tracking_error: number;
  information_ratio: number;
  basis: string;
  low_confidence: boolean;
};

export type PerformanceStatus =
  | "ok"
  | "stale"
  | "degraded"
  | "insufficient_data"
  | "unavailable";

export type PerformanceGatedValue = {
  value: number | null;
  n: number;
  min_n: number;
  unavailable_reason: string | null;
  low_confidence?: boolean;
};

export type PerformanceData = {
  as_of: string;
  last_sync: string;
  period_start: string;
  period_end: string;
  period_label: string;
  /** v1 carried the symbol string; v2 carries the whole block, or null. */
  benchmark: string | PerformanceBenchmarkBlock | null;
  benchmark_total_return?: number;
  trades_source: string;
  price_sources: {
    stocks: string;
    options: string;
  };
  methodology: {
    curve_type: string;
    return_basis: string;
    risk_free_rate: number;
    library_strategy: string;
    risk_free_source?: string;
    flow_convention?: string;
    day_count?: string;
  };
  summary: PerformanceSummary;
  warnings: Array<string | PerformanceWarning>;
  contracts_missing_history: string[];
  series: PerformanceSeriesPoint[];

  // ---- payload v2 (schema_version 2) ----
  schema_version?: number;
  status?: PerformanceStatus;
  generated_at?: string;
  account_id?: string;
  nav_source?: string;
  nav_as_of?: string;
  nav_sessions_behind?: number;
  flows_status?: string;
  flows_source?: string;
  calendar_days?: number;
  counts?: {
    n_nav_observations: number;
    n_subperiods: number;
    n_returns: number;
    n_skipped: number;
    n_suspect: number;
  };
  twr?: {
    cum_return: number | null;
    annualized: PerformanceGatedValue;
    excludes_suspect: boolean;
  };
  mwr?: {
    period_return: PerformanceGatedValue;
    annualized: PerformanceGatedValue;
    multiple_sign_changes: boolean;
  };
  risk?: Record<string, PerformanceGatedValue>;
  distribution?: Record<string, PerformanceGatedValue>;
  drawdown_detail?: {
    trough_date?: string | null;
    peak_date?: string | null;
    trough_days?: number | null;
    recovery_days?: number | null;
    ongoing?: boolean;
  };
  equity?: {
    starting: number;
    ending: number;
    net_external_flows: number;
    investment_pnl: number;
  };
  subperiods?: Array<Record<string, unknown>>;
};

// Trade journal types
export type TradeEdgeAnalysis = {
  edge_type: string;
  dp_flow?: string;
  dp_strength?: number;
  dp_buy_ratio?: number;
  [key: string]: unknown;
};

export type TradeEntry = {
  id: number;
  date: string;
  time?: string;
  ticker: string;
  company_name?: string;
  sector?: string;
  structure: string;
  decision: string;
  action?: string;
  contracts?: number;
  shares?: number;
  quantity?: number;
  fill_price?: number;
  entry_price?: number;
  total_cost?: number;
  entry_cost?: number;
  max_risk?: number;
  max_gain?: number;
  pct_of_bankroll?: number;
  gates_passed?: string[];
  gates_failed?: string[];
  edge_analysis?: TradeEdgeAnalysis;
  realized_pnl?: number;
  return_on_risk?: number;
  outcome?: string;
  close_date?: string;
  notes?: string;
  rule_violation?: string;
  thesis?: string;
  legs?: TradeLeg[];
};

export type TradeLeg = {
  type?: string;
  strike?: number;
  expiry?: string;
  open_price?: number;
  close_price?: number;
  leg_pnl?: number;
  contracts?: number;
  action?: string;
};

export type TradeLogData = {
  trades: TradeEntry[];
};

// Discover types
export type DiscoverCandidate = {
  ticker: string;
  score: number;
  score_breakdown: Record<string, number>;
  alerts: number;
  total_premium: number;
  calls: number;
  puts: number;
  options_bias: string;
  sweeps: number;
  avg_vol_oi: number;
  sector: string;
  issue_type: string;
  dp_direction: string;
  dp_strength: number;
  dp_buy_ratio: number;
  dp_sustained_days: number;
  dp_total_prints: number;
  confluence: boolean;
};

export type DiscoverData = {
  discovery_time: string;
  alerts_analyzed: number;
  candidates_found: number;
  candidates: DiscoverCandidate[];
  error?: string;
};

// Blotter types (historical trades from IB Flex Query)
export type BlotterExecution = {
  exec_id: string;
  time: string;
  side: string;
  quantity: number;
  price: number;
  commission: number;
  notional_value: number;
  net_cash_flow: number;
};

export type BlotterTrade = {
  symbol: string;
  contract_desc: string;
  sec_type: string;
  is_closed: boolean;
  net_quantity: number;
  total_quantity?: number;
  total_commission: number;
  realized_pnl: number | null;
  realized_quantity?: number;
  realized_cost_basis?: number | null;
  cost_basis: number;
  proceeds: number;
  total_cash_flow: number;
  executions: BlotterExecution[];
};

export type BlotterData = {
  as_of: string;
  summary: {
    closed_trades: number;
    open_trades: number;
    total_commissions: number;
    realized_pnl: number;
  };
  closed_trades: BlotterTrade[];
  open_trades: BlotterTrade[];
};

// Scanner types
export type ScannerSignal = {
  ticker: string;
  sector: string;
  score: number;
  signal: string;
  direction: string;
  strength: number;
  buy_ratio: number | null;
  num_prints: number;
  sustained_days: number;
  recent_direction: string;
  recent_strength: number;
  // F11 — Chronos-2 forecast band over recent flow_strength, attached by
  // the scanner when chronos is available. Absent on the lean fleet.
  forecast?: {
    score: number;
    band: { lo: number; median: number; hi: number };
    interval_width: number;
    upside_skew: number;
    convex_reinforced: boolean;
    horizon: number;
  };
};

export type ScannerData = {
  scan_time: string;
  tickers_scanned: number;
  signals_found: number;
  top_signals: ScannerSignal[];
};

// LEAP IV-mispricing scanner — surfaced via /api/leap, written by
// scripts/leap_scanner_uw.py --json. The script ranks tickers where
// long-dated IV diverges from realized vol; `best_gap` is the headline
// signal (HV − IV in vol points). `is_mispriced` is the script's own
// boolean classification.
/** The single contract behind `best_gap`, emitted by leap_scanner_uw.py so the
 *  scanner can deep-link it into the chain order builder. Absent on scans
 *  written before the field existed. */
export type LeapBestContract = {
  symbol: string;
  expiry: string;
  strike: number;
  right: "C" | "P";
  iv: number;
  delta: number;
  gap: number;
  oi: number;
  volume: number;
};

export type LeapResult = {
  ticker: string;
  price: number | null;
  hv_20: number | null;
  hv_60: number | null;
  hv_252: number | null;
  current_iv: number | null;
  iv_rank: number | null;
  leap_count: number;
  best_gap: number;
  is_mispriced: boolean;
  best_leap?: LeapBestContract | null;
};

export type LeapData = {
  scan_time: string;
  min_gap: number | null;
  /** "explicit" for custom ticker scans, "preset:<name>" for scheduled/preset scans. */
  universe?: string;
  requested_tickers?: string[];
  results: LeapResult[];
};

// GARCH convergence scanner — surfaced via /api/garch-convergence, written
// by scripts/garch_convergence.py --json. Scans correlated pairs for IV
// repricing lags. Each pair has a leader / lagger; the dashboard ranks
// pairs by `divergence` (composite metric) with `gates_passed` indicating
// which rows are actionable per the four-gate framework.
export type GarchPair = {
  pair: [string, string];
  leader: string;
  lagger: string;
  divergence: number;
  lagger_hv_iv_gap: number;
  lagger_iv_rank: number | null;
  signal: string;
  gates_passed: boolean;
  failing_gates: string[];
  expected_iv: number | null;
  expected_move: number | null;
};

export type GarchTickerVol = {
  price: number | null;
  hv20: number | null;
  hv60: number | null;
  hv252: number | null;
  leap_atm_iv: number | null;
  iv_rank: number | null;
  iv_hv60: number;
  hv20_minus_iv: number;
  has_leaps: boolean;
  leap_count: number;
  /** Set when the scan could not price this ticker (e.g. UW quota or auth
   *  failure on the IV-rank endpoint). `iv_rank` is null alongside it — the
   *  writer used to publish a fabricated 0, which is the most bullish value
   *  every downstream threshold tests for. R-199. */
  error?: string | null;
};

export type GarchConvergenceData = {
  scan_time: string;
  /** "explicit" for custom pair scans, "preset:<name>" for scheduled/preset scans. */
  universe?: string;
  requested_tickers?: string[];
  tickers: Record<string, GarchTickerVol>;
  pairs: GarchPair[];
};

export type ThetaHarvesterLeg = {
  symbol: string;
  expiry: string;
  strike: number;
  right: "C" | "P";
  iv: number;
  delta: number;
  theta: number;
  gamma: number;
  vega: number;
  bid?: number | null;
  ask?: number | null;
  volume: number;
  open_interest: number;
};

export type ThetaHarvesterStructure = {
  expiry: string;
  dte: number;
  short_put: ThetaHarvesterLeg;
  short_call: ThetaHarvesterLeg;
  net_delta: number;
  theta: number;
  gamma: number;
  vega: number;
  credit?: number | null;
};

/** Next earnings release relative to the short-strangle DTE window. Null when unknown. */
export type ThetaHarvesterEarnings = {
  report_date: string;
  report_time: "premarket" | "postmarket" | "unknown" | string | null;
  days_until: number | null;
  within_dte: boolean | null;
  source?: string | null;
  expected_move_pct?: number | null;
};

export type ThetaHarvesterResult = {
  ticker: string;
  score: number;
  verdict: "THETA_HARVEST" | "WATCHLIST" | "DIRECTIONAL_DISGUISE" | string;
  structure: ThetaHarvesterStructure;
  spot: number;
  iv: number;
  hv20: number;
  hv60: number;
  iv_rv_edge: number;
  iv_rv_ratio: number;
  trend_20d_pct: number;
  range_score: number;
  dealer_support: "SUPPORT" | "NO_SUPPORT" | "UNKNOWN" | string;
  net_gex: number | null;
  gex_flip: number | null;
  setup: string;
  gates: Record<string, boolean>;
  errors: string[];
  /** Next earnings if known; null/omitted when unavailable. */
  earnings?: ThetaHarvesterEarnings | null;
};

export type ThetaHarvesterData = {
  scan_time: string;
  source: string;
  universe: string;
  requested_tickers?: string[];
  tickers_scanned: number;
  candidates_found: number;
  theta_harvest_count: number;
  results: ThetaHarvesterResult[];
};

export type StrengthFactorCheck = {
  label: string;
  passed: boolean;
  value: number | null;
  threshold: string;
  note: string;
  source: "UW" | "APPROX" | string;
};

export type StrengthFactorAssessment = {
  group: string;
  passed: boolean;
  checks_passed: number;
  checks_total: number;
  source: "UW" | "APPROX" | string;
  checks: StrengthFactorCheck[];
  notes: string[];
};

export type StrengthConfirmationResult = {
  ticker: string;
  verdict: "REAL_STRENGTH_CONFIRMED" | "WATCHLIST" | "WEAK" | string;
  score: number;
  groups_passed: number;
  spot: number;
  factors: StrengthFactorAssessment[];
  errors: string[];
};

export type StrengthConfirmationData = {
  scan_time: string;
  source: string;
  universe: string;
  requested_tickers?: string[];
  tickers_scanned: number;
  candidates_found: number;
  confirmed_strength_count: number;
  results: StrengthConfirmationResult[];
};

// Flow Analysis types
export type FlowAnalysisPosition = {
  ticker: string;
  position: string;
  direction: string;
  flow_direction: string;
  flow_label: string;
  flow_class: string;
  strength: number;
  buy_ratio: number | null;
  daily_buy_ratios?: { date: string; buy_ratio: number | null }[];
  note: string;
};

export type FlowAnalysisData = {
  analysis_time: string;
  positions_scanned: number;
  supports: FlowAnalysisPosition[];
  against: FlowAnalysisPosition[];
  watch: FlowAnalysisPosition[];
  neutral: FlowAnalysisPosition[];
};

// Real-time pricing types
export type PriceData = {
  symbol: string;
  last: number | null;
  lastIsCalculated: boolean;
  bid: number | null;
  ask: number | null;
  bidSize: number | null;
  askSize: number | null;
  volume: number | null;
  high: number | null;
  low: number | null;
  open: number | null;
  close: number | null;
  // Misc Stats (generic tick 165)
  week52High: number | null;
  week52Low: number | null;
  avgVolume: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  impliedVol: number | null;
  undPrice: number | null;
  timestamp: string;
};

export type PriceUpdate = {
  symbol: string;
  data: PriceData;
  receivedAt: Date;
};

// Attribution types
export type StrategyAttribution = {
  strategy_id: string;
  strategy_name: string;
  trade_count: number;
  closed_count: number;
  open_count: number;
  winners: number;
  losers: number;
  realized_pnl: number;
  total_cost: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  expected_win_rate: number | null;
  kelly_accuracy: number | null;
};

export type TickerAttributionEntry = {
  ticker: string;
  trade_count: number;
  realized_pnl: number;
  strategies: string[];
};

export type EdgeAttribution = {
  edge_type: string;
  trade_count: number;
  closed_count: number;
  realized_pnl: number;
  win_rate: number | null;
  winners: number;
  losers: number;
};

export type RiskAttribution = {
  risk_type: string;
  trade_count: number;
  closed_count: number;
  realized_pnl: number;
  win_rate: number | null;
  winners: number;
  losers: number;
};

export type KellyCalibrationEntry = {
  expected_win_rate: number | null;
  actual_win_rate: number | null;
  accuracy: number | null;
  sample_size: number;
};

export type AttributionData = {
  total_trades: number;
  closed_trades: number;
  open_trades: number;
  total_realized_pnl: number;
  by_strategy: StrategyAttribution[];
  by_ticker: TickerAttributionEntry[];
  by_edge: EdgeAttribution[];
  by_risk: RiskAttribution[];
  best_ticker: string | null;
  worst_ticker: string | null;
  kelly_calibration: Record<string, KellyCalibrationEntry>;
};
