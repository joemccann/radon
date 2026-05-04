import type { AccountSummary } from "./types";

export type MarginLevel = "none" | "warning" | "critical";

export interface MarginAssessment {
  level: MarginLevel;
  message: string;
  /** excess_liquidity / net_liquidation × 100. null when inputs missing. */
  cushionPct: number | null;
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
    return { level: "none", message: "", cushionPct: null, key: "none:no-account" };
  }

  const nlv = account.net_liquidation;
  const el = account.excess_liquidity;
  const mmr = account.maintenance_margin;
  const ewl = account.equity_with_loan;

  if (nlv == null || nlv <= 0 || el == null) {
    return { level: "none", message: "", cushionPct: null, key: "none:missing" };
  }

  // Active margin call — Excess Liquidity has crossed zero.
  if (el <= 0) {
    return {
      level: "critical",
      message: `Margin call: Excess Liquidity is ${el < 0 ? "−" : ""}${fmtUsd(el)}. Reduce positions or deposit funds immediately.`,
      cushionPct: 0,
      key: "critical:excess-liquidity-non-positive",
    };
  }

  const cushion = el / nlv;
  const cushionPct = cushion * 100;

  if (cushion < 0.01) {
    return {
      level: "critical",
      message: `Margin cushion ${cushionPct.toFixed(2)}% (Excess Liquidity ${fmtUsd(el)}). Margin call imminent.`,
      cushionPct,
      key: "critical:cushion-below-1pct",
    };
  }

  if (cushion < 0.05) {
    return {
      level: "warning",
      message: `Margin cushion ${cushionPct.toFixed(1)}% (Excess Liquidity ${fmtUsd(el)}). Approaching margin call.`,
      cushionPct,
      key: "warning:cushion-below-5pct",
    };
  }

  // IBKR's official warning rule per their glossary: EquityWithLoanValue ≤ MaintMarginReq × 1.10.
  if (ewl != null && mmr != null && mmr > 0 && ewl <= mmr * 1.1) {
    return {
      level: "warning",
      message: `Equity with loan ${fmtUsd(ewl)} is within 10% of maintenance margin ${fmtUsd(mmr)}. Margin warning per IBKR rule.`,
      cushionPct,
      key: "warning:ewl-within-10pct-of-mmr",
    };
  }

  return { level: "none", message: "", cushionPct, key: "none:healthy" };
}
