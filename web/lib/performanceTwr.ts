/**
 * TWR performance helpers — Plan §4.3-4.4.
 * Single source of truth for methodology constants and N-gates.
 */

export const TWR_CURVE_TYPE = "twr_modified_dietz_daily" as const;
export const TWR_RETURN_BASIS = "time_weighted" as const;
export const TWR_LIBRARY_STRATEGY = "fred_dgs3mo" as const;
export const TRADING_DAYS = 252;

export type TwrMethodology = {
  curve_type: typeof TWR_CURVE_TYPE;
  return_basis: typeof TWR_RETURN_BASIS;
  risk_free_rate: number;
  library_strategy: typeof TWR_LIBRARY_STRATEGY;
};

export function buildTwrMethodology(riskFreeRate: number): TwrMethodology {
  return {
    curve_type: TWR_CURVE_TYPE,
    return_basis: TWR_RETURN_BASIS,
    risk_free_rate: riskFreeRate,
    library_strategy: TWR_LIBRARY_STRATEGY,
  };
}

/**
 * Annualized TWR with N gate: null when N<20.
 * Matches scripts/perf_twr_builder.py:compute_annualized_return.
 */
export function annualizedTwr(totalReturn: number, n: number): number | null {
  if (n < 20) return null;
  return Math.pow(1 + totalReturn, TRADING_DAYS / Math.max(n, 1)) - 1;
}

/**
 * Beta/alpha gate: null when N<40.
 */
export function isBetaGated(n: number): boolean {
  return n < 40;
}

/**
 * VaR/CVaR gate: null when N<60.
 */
export function isVarGated(n: number): boolean {
  return n < 60;
}

export type TwrPayload = {
  methodology: TwrMethodology;
  summary: {
    total_return: number;
    annualized_return: number | null;
    trading_days: number;
    period_start?: string;
    period_end?: string;
  };
  metrics?: {
    total_return: number;
    annualized_return: number | null;
    beta: number | null;
    alpha: number | null;
    var_95: number | null;
    cvar_95: number | null;
  };
  warnings?: string[];
  status?: string;
};
