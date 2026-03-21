import { Type, type Static } from "@sinclair/typebox";

// ── Input (maps to argparse) ──────────────────────────────────────────

export const IBSyncInput = Type.Object({
  host: Type.Optional(Type.String({ description: "TWS/Gateway host" })),
  port: Type.Optional(Type.Number({ description: "TWS/Gateway port" })),
  clientId: Type.Optional(Type.Number({ description: "Client ID" })),
  sync: Type.Optional(Type.Boolean({ description: "Sync to portfolio.json" })),
  noPrices: Type.Optional(Type.Boolean({ description: "Skip market price fetch" })),
});

export type IBSyncInput = Static<typeof IBSyncInput>;

// ── Output (matches data/portfolio.json shape) ────────────────────────

const PortfolioLeg = Type.Object({
  direction: Type.String(),
  contracts: Type.Number(),
  type: Type.String(),
  strike: Type.Union([Type.Number(), Type.Null()]),
  entry_cost: Type.Number(),
  avg_cost: Type.Number(),
  market_price: Type.Union([Type.Number(), Type.Null()]),
  market_value: Type.Union([Type.Number(), Type.Null()]),
  market_price_is_calculated: Type.Optional(Type.Boolean()),
});

const PortfolioPosition = Type.Object({
  id: Type.Number(),
  ticker: Type.String(),
  structure: Type.String(),
  structure_type: Type.String(),
  risk_profile: Type.String(),
  expiry: Type.String(),
  contracts: Type.Number(),
  direction: Type.String(),
  entry_cost: Type.Number(),
  max_risk: Type.Union([Type.Number(), Type.Null()]),
  market_value: Type.Union([Type.Number(), Type.Null()]),
  legs: Type.Array(PortfolioLeg),
  market_price_is_calculated: Type.Optional(Type.Boolean()),
  /** IB's per-position daily P&L from reqPnLSingle.
   *  Correctly handles intraday additions (overnight contracts use
   *  yesterday's close; today's adds use fill price as reference). */
  ib_daily_pnl: Type.Optional(Type.Union([Type.Number(), Type.Null()])),
  kelly_optimal: Type.Union([Type.Number(), Type.Null()]),
  target: Type.Union([Type.Number(), Type.Null()]),
  stop: Type.Union([Type.Number(), Type.Null()]),
  entry_date: Type.String(),
});

const AccountSummary = Type.Object({
  net_liquidation: Type.Number(),
  daily_pnl: Type.Union([Type.Number(), Type.Null()]),
  unrealized_pnl: Type.Number(),
  realized_pnl: Type.Number(),
  cash: Type.Optional(Type.Number()),
  settled_cash: Type.Number(),
  maintenance_margin: Type.Number(),
  initial_margin: Type.Optional(Type.Number()),
  excess_liquidity: Type.Number(),
  buying_power: Type.Number(),
  available_funds: Type.Optional(Type.Number()),
  dividends: Type.Number(),
  equity_with_loan: Type.Optional(Type.Number()),
  previous_day_ewl: Type.Optional(Type.Number()),
  reg_t_equity: Type.Optional(Type.Number()),
  sma: Type.Optional(Type.Number()),
  gross_position_value: Type.Optional(Type.Number()),
});

export const PortfolioData = Type.Object({
  bankroll: Type.Number(),
  peak_value: Type.Number(),
  last_sync: Type.String(),
  positions: Type.Array(PortfolioPosition),
  total_deployed_pct: Type.Number(),
  total_deployed_dollars: Type.Number(),
  remaining_capacity_pct: Type.Number(),
  position_count: Type.Number(),
  defined_risk_count: Type.Number(),
  undefined_risk_count: Type.Number(),
  avg_kelly_optimal: Type.Union([Type.Number(), Type.Null()]),
  account_summary: Type.Optional(AccountSummary),
});

export type PortfolioData = Static<typeof PortfolioData>;
