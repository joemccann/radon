/**
 * Black-Scholes option pricing — pure TS port of scripts/scenario_analysis.py:192-226.
 *
 * Mirrors the Python reference exactly so frontend "implied value" displays
 * agree with backend scenario analysis at the same inputs.
 */

export const RISK_FREE_RATE_DEFAULT = 0.0;
export const DIVIDEND_YIELD_DEFAULT = 0.0;
export const SIGMA_FLOOR = 0.001;

const SQRT_2 = Math.sqrt(2);

/**
 * Cumulative normal distribution. Abramowitz & Stegun 26.2.17.
 * Line-for-line port of scenario_analysis.norm_cdf.
 */
export function normCdf(x: number): number {
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;

  const sign = x < 0 ? -1 : 1;
  const absX = Math.abs(x) / SQRT_2;
  const t = 1.0 / (1.0 + p * absX);
  const y =
    1.0 -
    (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) *
      t *
      Math.exp(-absX * absX);

  return 0.5 * (1.0 + sign * y);
}

export type BsInputs = {
  S: number;
  K: number;
  T: number;
  r: number;
  sigma: number;
  type: "Call" | "Put";
};

export function bsPrice({ S, K, T, r, sigma, type }: BsInputs): number {
  return type === "Call" ? bsCall(S, K, T, r, sigma) : bsPut(S, K, T, r, sigma);
}

export function bsCall(S: number, K: number, T: number, r: number, sigma: number): number {
  if (T <= 0) return Math.max(S - K, 0);
  if (sigma <= SIGMA_FLOOR) return Math.max(S - K * Math.exp(-r * T), 0);
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT);
  const d2 = d1 - sigma * sqrtT;
  return S * normCdf(d1) - K * Math.exp(-r * T) * normCdf(d2);
}

export function bsPut(S: number, K: number, T: number, r: number, sigma: number): number {
  if (T <= 0) return Math.max(K - S, 0);
  if (sigma <= SIGMA_FLOOR) return Math.max(K * Math.exp(-r * T) - S, 0);
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT);
  const d2 = d1 - sigma * sqrtT;
  return K * Math.exp(-r * T) * normCdf(-d2) - S * normCdf(-d1);
}

/**
 * Back-solve implied volatility from an observed option price using bisection
 * over [σ_low, σ_high]. Returns null when the observed price is outside no-
 * arbitrage bounds — both indicate a usable σ doesn't exist.
 *
 * Used when streaming impliedVol is unavailable (market closed; IB stops
 * sending tickOptionComputation outside RTH): fall back to yesterday's option
 * close + underlying close to recover σ, then re-price at today's spot.
 */
const IV_SIGMA_LOW = 0.001;
const IV_SIGMA_HIGH = 5.0;
const IV_TOLERANCE = 1e-6;
const IV_MAX_ITER = 100;

export function bsImpliedVol(
  observedPrice: number,
  S: number,
  K: number,
  T: number,
  r: number,
  type: "Call" | "Put",
): number | null {
  if (!Number.isFinite(observedPrice) || observedPrice <= 0) return null;
  if (!Number.isFinite(S) || S <= 0) return null;
  if (!Number.isFinite(K) || K <= 0) return null;
  if (!Number.isFinite(T) || T <= 0) return null;

  const intrinsic =
    type === "Call"
      ? Math.max(S - K * Math.exp(-r * T), 0)
      : Math.max(K * Math.exp(-r * T) - S, 0);
  const upperBound = type === "Call" ? S : K * Math.exp(-r * T);
  if (observedPrice <= intrinsic + IV_TOLERANCE) return null;
  if (observedPrice >= upperBound - IV_TOLERANCE) return null;

  const f = (sigma: number) => bsPrice({ S, K, T, r, sigma, type }) - observedPrice;

  let lo = IV_SIGMA_LOW;
  let hi = IV_SIGMA_HIGH;
  let fLo = f(lo);
  let fHi = f(hi);
  if (fLo === 0) return lo;
  if (fHi === 0) return hi;
  if (fLo * fHi > 0) return null;

  for (let i = 0; i < IV_MAX_ITER; i++) {
    const mid = 0.5 * (lo + hi);
    const fMid = f(mid);
    if (Math.abs(fMid) < IV_TOLERANCE || (hi - lo) * 0.5 < IV_TOLERANCE) {
      return mid;
    }
    if (fLo * fMid < 0) {
      hi = mid;
      fHi = fMid;
    } else {
      lo = mid;
      fLo = fMid;
    }
  }
  return 0.5 * (lo + hi);
}
