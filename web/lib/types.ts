import type { LayoutDashboard } from "lucide-react";

export type MessageRole = "assistant" | "user";

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

export type AssistantResponse = {
  content?: string;
  model?: string;
  error?: string;
};

export type PiResponse = {
  command: string;
  status: "ok" | "error";
  output: string;
  stderr?: string;
  error?: string;
};

export type WorkspaceSection = "dashboard" | "flow-analysis" | "portfolio" | "orders" | "scanner" | "discover" | "journal" | "regime" | "cta";

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export type WorkspaceNavItem = {
  label: string;
  route: WorkspaceSection;
  href: string;
  icon: typeof LayoutDashboard;
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
  kelly_optimal: number | null;
  target: number | null;
  stop: number | null;
  entry_date: string;
};

export type OrderContract = {
  conId: number | null;
  symbol: string;
  secType: string;
  strike: number | null;
  right: string | null;
  expiry: string | null;
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
  total_commission: number;
  realized_pnl: number;
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
};

export type ScannerData = {
  scan_time: string;
  tickers_scanned: number;
  signals_found: number;
  top_signals: ScannerSignal[];
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
