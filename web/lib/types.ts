import type { ComponentType } from "react";

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
};

export type AssistantOrderProposal = {
  tool: string;
  destructive: true;
  input: Record<string, unknown>;
  summary: string;
  toolUseId: string;
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

export type WorkspaceSection = "dashboard" | "flow-analysis" | "portfolio" | "performance" | "orders" | "scanner" | "discover" | "journal" | "regime" | "cta" | "alerts" | "workflow" | "ticker-detail" | "admin" | "profile";

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export type WorkspaceNavItem = {
  label: string;
  route: WorkspaceSection;
  href: string;
  icon: NavIcon;
  hidden?: boolean;
};

export type PortfolioLeg = {
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

export type PortfolioPosition = {
  id: number;
  ticker: string;
  structure: string;
  structure_type: string;
  risk_profile: string;
  expiry: string;
  contracts: number;
  direction: string;
  entry_cost: number;
  max_risk: number | null;
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
};

export type ExecutedOrder = {
  execId: string;
  symbol: string;
  contract: OrderContract;
  side: string;
  quantity: number;
  avgPrice: number | null;
  commission: number | null;
  realizedPNL: number | null;
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
  account_summary?: AccountSummary;
  /** Ticker -> earliest journal trade date for entry time on share cards. */
  trade_log_dates?: Record<string, string>;
};

export type PerformanceSeriesPoint = {
  date: string;
  equity: number;
  daily_return: number | null;
  drawdown: number;
  benchmark_close: number;
  benchmark_return: number;
};

export type PerformanceSummary = {
  starting_equity: number;
  ending_equity: number;
  pnl: number;
  trading_days: number;
  total_return: number;
  annualized_return: number;
  annualized_volatility: number;
  downside_deviation: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  max_drawdown: number;
  current_drawdown: number;
  max_drawdown_duration_days: number;
  beta: number;
  alpha: number;
  correlation: number;
  r_squared: number;
  tracking_error: number;
  information_ratio: number;
  treynor_ratio: number;
  upside_capture: number;
  downside_capture: number;
  var_95: number;
  cvar_95: number;
  tail_ratio: number;
  ulcer_index: number;
  skew: number;
  kurtosis: number;
  hit_rate: number;
  positive_days: number;
  negative_days: number;
  flat_days: number;
  best_day: number;
  worst_day: number;
  average_up_day: number;
  average_down_day: number;
  win_loss_ratio: number;
};

export type PerformanceData = {
  as_of: string;
  last_sync: string;
  period_start: string;
  period_end: string;
  period_label: string;
  benchmark: string;
  benchmark_total_return: number;
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
  };
  summary: PerformanceSummary;
  warnings: string[];
  contracts_missing_history: string[];
  series: PerformanceSeriesPoint[];
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
};

export type LeapData = {
  scan_time: string;
  min_gap: number | null;
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
};

export type GarchConvergenceData = {
  scan_time: string;
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
