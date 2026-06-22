import type { AccountSummary } from "./types";

export type MarginLevel = "none" | "warning" | "critical";

export interface MarginAssessment {
  level: MarginLevel;
  message: string;
  /** excess_liquidity / net_liquidation × 100. null when inputs missing. */
  cushionPct: number | null;
  /**
   * Minimum cash DEPOSIT (USD, rounded up to the nearest dollar) that clears the
   * position out of every warning/critical condition to "none". null when level is
   * "none" or inputs are missing. A deposit raises equity_with_loan, net_liquidation,
   * and excess_liquidity each by D and leaves maint_margin_req unchanged.
   */
  cashToClear: number | null;
  /** Stable identifier for transition logic. Hashes the (level, rounded cushion) so the same condition produces the same key across polls but a worsening triggers a fresh fire. */
  key: string;
}

const RANK: Record<MarginLevel, number> = { none: 0, warning: 1, critical: 2 };

export function rankOf(level: MarginLevel): number {
  return RANK[level];
}

function fmtUsd(n: number): string {
  return `$${Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

interface MarginInputs {
  nlv: number;
  el: number;
  mmr: number | null;
  ewl: number | null;
}

/**
 * Minimum cash deposit D that clears every binding constraint to "none".
 *
 * A deposit of D raises equity_with_loan, net_liquidation, and excess_liquidity each
 * by D; maint_margin_req is unchanged (cash carries no maintenance requirement). Solve
 * each constraint for D and take the max:
 *   IBKR-rule:        equity + D > maint × 1.10        → D > maint×1.10 − equity
 *   excess-liquidity: excess_liquidity + D > 0         → D > −excess_liquidity
 *   cushion 5%:       (excess_liquidity + D)/(nlv + D) ≥ 0.05
 *                                                      → D ≥ (0.05×nlv − excess_liquidity)/0.95
 *
 * Returns ceil(max(0, binding constraint)) so the suggested deposit strictly clears.
 */
function cashToClearWarning({ nlv, el, mmr, ewl }: MarginInputs): number {
  const constraints: number[] = [
    -el, // excess liquidity > 0
    (0.05 * nlv - el) / 0.95, // cushion ≥ 5%
  ];
  if (ewl != null && mmr != null && mmr > 0) {
    constraints.push(mmr * 1.1 - ewl); // EWL > MMR × 1.10
  }
  const binding = Math.max(...constraints);
  return Math.max(0, Math.ceil(binding));
}

function depositSuffix(cashToClear: number): string {
  if (cashToClear <= 0) return "";
  return ` Deposit ~${fmtUsd(cashToClear)} in cash to clear this warning.`;
}

/**
 * Derive a margin warning from IBKR account-summary fields.
 *
 * Thresholds match IBKR's published guidance:
 *   - critical: Excess Liquidity ≤ 0 (active margin call)
 *   - critical: cushion < 1%   (margin call imminent)
 *   - warning:  cushion < 5%   (approaching)
 *   - warning:  EquityWithLoanValue ≤ MaintMarginReq × 1.10  (IBKR's own published rule)
 *   - none:     otherwise
 *
 * Returns level=none with cushionPct=null when inputs are missing or
 * net_liquidation is non-positive (avoid division-by-zero / cry-wolf).
 */
export function assessMargin(account: AccountSummary | null | undefined): MarginAssessment {
  if (!account) {
    return { level: "none", message: "", cushionPct: null, cashToClear: null, key: "none:no-account" };
  }

  const nlv = account.net_liquidation;
  const el = account.excess_liquidity;
  const mmr = account.maintenance_margin ?? null;
  const ewl = account.equity_with_loan ?? null;

  if (nlv == null || nlv <= 0 || el == null) {
    return { level: "none", message: "", cushionPct: null, cashToClear: null, key: "none:missing" };
  }

  const cashToClear = cashToClearWarning({ nlv, el, mmr, ewl });
  const deposit = depositSuffix(cashToClear);

  // Active margin call — Excess Liquidity has crossed zero.
  if (el <= 0) {
    return {
      level: "critical",
      message: `Margin call: Excess Liquidity is ${el < 0 ? "−" : ""}${fmtUsd(el)}. Reduce positions or deposit funds immediately.${deposit}`,
      cushionPct: 0,
      cashToClear,
      key: "critical:excess-liquidity-non-positive",
    };
  }

  const cushion = el / nlv;
  const cushionPct = cushion * 100;

  if (cushion < 0.01) {
    return {
      level: "critical",
      message: `Margin cushion ${cushionPct.toFixed(2)}% (Excess Liquidity ${fmtUsd(el)}). Margin call imminent.${deposit}`,
      cushionPct,
      cashToClear,
      key: "critical:cushion-below-1pct",
    };
  }

  if (cushion < 0.05) {
    return {
      level: "warning",
      message: `Margin cushion ${cushionPct.toFixed(1)}% (Excess Liquidity ${fmtUsd(el)}). Approaching margin call.${deposit}`,
      cushionPct,
      cashToClear,
      key: "warning:cushion-below-5pct",
    };
  }

  // IBKR's official warning rule per their glossary: EquityWithLoanValue ≤ MaintMarginReq × 1.10.
  if (ewl != null && mmr != null && mmr > 0 && ewl <= mmr * 1.1) {
    return {
      level: "warning",
      message: `Equity with loan ${fmtUsd(ewl)} is within 10% of maintenance margin ${fmtUsd(mmr)}. Margin warning per IBKR rule.${deposit}`,
      cushionPct,
      cashToClear,
      key: "warning:ewl-within-10pct-of-mmr",
    };
  }

  return { level: "none", message: "", cushionPct, cashToClear: null, key: "none:healthy" };
}
