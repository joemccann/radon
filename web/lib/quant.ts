/**
 * Quant sleeve (docs/quant-strategies.md): payload types + pure helpers for
 * the QUANT regime tab. Mirrors GET /api/quant/[strategy], which reads the
 * Turso tables the Python runner writes (quant_runs, quant_targets,
 * quant_orders). The sleeve runs outside the Four Gates; nothing here is a
 * trade signal.
 */

import { presetRange, RANGE_PRESETS, type RangePreset, type RangePresetSlug } from "./historyRange";

/**
 * One row per `QuantStrategy` in scripts/quant/strategies.py, same order.
 * Pinned by tests/quant-registry-pin.test.ts, which parses the Python file.
 */
export const QUANT_STRATEGIES = [
  { key: "trend_inverse_vol", label: "Trend, inverse vol (futures proxy)" },
  { key: "inverse_vol", label: "Monthly inverse volatility" },
  { key: "monthly_rotation", label: "Monthly momentum rotation" },
  { key: "dual_momentum", label: "Quarterly dual momentum" },
  { key: "vol_target_trend", label: "Vol-targeted trend" },
  { key: "signal_change_defensive", label: "Signal-change defensive rotation" },
] as const;

export type QuantStrategyKey = (typeof QUANT_STRATEGIES)[number]["key"];

export const DEFAULT_QUANT_STRATEGY: QuantStrategyKey = "trend_inverse_vol";

export function isQuantStrategyKey(key: string): key is QuantStrategyKey {
  return QUANT_STRATEGIES.some((s) => s.key === key);
}

export function quantLabel(key: string): string {
  return QUANT_STRATEGIES.find((s) => s.key === key)?.label ?? key;
}

export interface QuantMetrics {
  sharpe: number | null;
  sortino?: number | null;
  calmar?: number | null;
  max_drawdown: number | null;
  hit_rate?: number | null;
  expectancy?: number | null;
  annualized_return: number | null;
  ann_vol: number | null;
  total_return?: number | null;
  avg_turnover?: number | null;
  cost_drag?: number | null;
  mean_gross?: number | null;
  mean_net?: number | null;
}

export interface QuantRun {
  run_at: string;
  since: string | null;
  until: string | null;
  n_sessions: number;
  n_rebalances: number;
  cost_bps: number | null;
  universe: string[];
  metrics: QuantMetrics;
  final_weights: Record<string, number>;
  dates: string[];
  equity: number[];
}

export interface QuantTargetRow {
  symbol: string;
  /** Signed fraction of the sleeve. */
  weight: number;
  /** weight x sleeve NAV at target time; null when no sleeve NAV was given. */
  notional: number | null;
  /** Close the weights were computed on; null before migration 0110. */
  close: number | null;
}

export interface QuantTargets {
  as_of: string;
  recorded_at: string | null;
  rebalance_today: boolean;
  rows: QuantTargetRow[];
}

export interface QuantOrderRow {
  symbol: string;
  action: string;
  quantity: number;
  limit_price: number | null;
  status: string;
  message: string | null;
}

export interface QuantOrders {
  as_of: string;
  placed_at: string | null;
  rows: QuantOrderRow[];
}

export interface QuantData {
  strategy: string;
  label?: string;
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  run: QuantRun | null;
  targets: QuantTargets | null;
  orders: QuantOrders | null;
}

export function QUANT_MISSING(strategy: string): QuantData {
  return { strategy, missing: true, run: null, targets: null, orders: null };
}

function finite(x: number | null | undefined): x is number {
  return x != null && Number.isFinite(x);
}

/** Fraction in, signed percent out: 0.059 -> "+5.9%". */
export function formatSignedPct(x: number | null | undefined, digits = 1): string {
  if (!finite(x)) return "---";
  const pct = x * 100;
  const sign = pct > 0 ? "+" : pct < 0 ? "-" : "";
  return `${sign}${Math.abs(pct).toFixed(digits)}%`;
}

/** Signed three-decimal weight: 0.25 -> "+0.250", 0 -> "0.000". */
export function formatWeight(w: number | null | undefined): string {
  if (!finite(w)) return "---";
  const sign = w > 0 ? "+" : w < 0 ? "-" : "";
  return `${sign}${Math.abs(w).toFixed(3)}`;
}

/** Whole-dollar notional with a thousands separator. */
export function formatUsd(n: number | null | undefined): string {
  if (!finite(n)) return "---";
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

/** Two-decimal ratio (Sharpe, Sortino). */
export function formatRatio(x: number | null | undefined): string {
  if (!finite(x)) return "---";
  return x.toFixed(2);
}

/** Price with two decimals. */
export function formatPrice(x: number | null | undefined): string {
  if (!finite(x)) return "---";
  return x.toFixed(2);
}

export interface QuantEquityRow {
  date: string;
  equity: number;
  /** Running peak-to-trough fraction, 0 at a new high, negative below it. */
  drawdown: number;
}

/** Zip dates and equity; drawdown is equity over its running peak minus one. */
export function buildEquityRows(run: Pick<QuantRun, "dates" | "equity"> | null | undefined): QuantEquityRow[] {
  if (!run) return [];
  const rows: QuantEquityRow[] = [];
  let peak = Number.NEGATIVE_INFINITY;
  const n = Math.min(run.dates.length, run.equity.length);
  for (let i = 0; i < n; i++) {
    const equity = run.equity[i];
    const date = run.dates[i];
    if (!finite(equity) || equity <= 0 || !date) continue;
    if (equity > peak) peak = equity;
    rows.push({ date, equity, drawdown: equity / peak - 1 });
  }
  return rows;
}

export type QuantRangeSlug = Extract<RangePresetSlug, "1y" | "3y" | "5y" | "all">;

export const QUANT_RANGE_PRESETS: ReadonlyArray<RangePreset> = RANGE_PRESETS.filter((p) =>
  (["1y", "3y", "5y", "all"] as RangePresetSlug[]).includes(p.slug),
);

export function quantPresetRange(slug: QuantRangeSlug, total: number): [number, number] {
  return presetRange(slug, total);
}

/** sign(weight) x floor(|notional| / close); null without a notional or a usable close. */
export function estimatedShares(row: Pick<QuantTargetRow, "weight" | "notional" | "close">): number | null {
  if (!finite(row.notional) || !finite(row.close) || row.close <= 0) return null;
  const shares = Math.floor(Math.abs(row.notional) / row.close);
  return Math.sign(row.weight) * shares;
}

export function grossNotional(rows: ReadonlyArray<Pick<QuantTargetRow, "notional">>): number {
  return rows.reduce((sum, r) => sum + (finite(r.notional) ? Math.abs(r.notional) : 0), 0);
}

/** Unsigned percent for a magnitude such as annualised vol: 0.082 -> "8.2%". */
export function formatPct(x: number | null | undefined, digits = 1): string {
  if (!finite(x)) return "---";
  return `${(x * 100).toFixed(digits)}%`;
}
