import { bsImpliedVol, normCdf, RISK_FREE_RATE_DEFAULT } from "./blackScholes";
import { yearsToExpiry } from "./impliedValue";
import { optionKey, type PriceData } from "./pricesProtocol";
import { detectStructure, normalizeComboOrder, type OrderLeg } from "./optionsChainUtils";

type VolSource = "stream" | "mid" | "last" | "close";

export type ShortStrangleSkewLeg = {
  strike: number;
  iv: number;
  delta: number;
  source: VolSource;
};

export type ShortStrangleSkewReady = {
  status: "ready";
  call: ShortStrangleSkewLeg;
  put: ShortStrangleSkewLeg;
  spot: number;
  expiry: string;
  quantity: number;
  skewVolPts: number;
  skewSide: "CALL" | "PUT" | "FLAT";
  netDeltaPerCombo: number;
  deltaSharesPerCombo: number;
  deltaSharesTotal: number;
};

export type ShortStrangleSkewUnavailable = {
  status: "unavailable";
  reason: "missing-spot" | "missing-vol";
};

export type ShortStrangleSkewResult = ShortStrangleSkewReady | ShortStrangleSkewUnavailable;

export type ShortStrangleSkewInput = {
  ticker: string;
  legs: OrderLeg[];
  prices: Record<string, PriceData>;
  spot?: number | null;
  now?: Date;
  riskFreeRate?: number;
};

const DELTA_EPS = 1e-9;

function positive(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function validProviderDelta(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= -1 - DELTA_EPS && value <= 1 + DELTA_EPS;
}

function midPrice(pd: PriceData | null | undefined): number | null {
  if (positive(pd?.bid) && positive(pd?.ask) && pd!.ask! >= pd!.bid!) return (pd!.bid! + pd!.ask!) / 2;
  return null;
}

function observedPrice(pd: PriceData | null | undefined): { price: number; source: Exclude<VolSource, "stream"> } | null {
  const mid = midPrice(pd);
  if (mid != null) return { price: mid, source: "mid" };
  if (positive(pd?.last)) return { price: pd!.last!, source: "last" };
  if (positive(pd?.close)) return { price: pd!.close!, source: "close" };
  return null;
}

function resolveSpot(input: ShortStrangleSkewInput, expiry: string): number | null {
  if (positive(input.spot)) return input.spot;
  const tickerPd = input.prices[input.ticker.toUpperCase()];
  const expiryKey = expiry.replace(/\D/g, "").slice(0, 8);
  if (tickerPd?.fwdCurve && positive(tickerPd.fwdCurve[expiryKey])) return tickerPd.fwdCurve[expiryKey]!;
  if (positive(tickerPd?.fwd)) return tickerPd!.fwd!;
  if (positive(tickerPd?.last)) return tickerPd!.last!;
  if (positive(tickerPd?.bid) && positive(tickerPd?.ask)) return (tickerPd!.bid! + tickerPd!.ask!) / 2;
  return null;
}

function optionDelta(right: "C" | "P", spot: number, strike: number, T: number, r: number, sigma: number): number {
  if (T <= 0 || !positive(sigma)) {
    if (right === "C") return spot > strike ? 1 : 0;
    return spot < strike ? -1 : 0;
  }
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(spot / strike) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT);
  const callDelta = normCdf(d1);
  return right === "C" ? callDelta : callDelta - 1;
}

function resolveLeg(
  ticker: string,
  leg: OrderLeg,
  pd: PriceData | null | undefined,
  tickerPd: PriceData | null | undefined,
  spot: number,
  T: number,
  r: number,
): ShortStrangleSkewLeg | null {
  if (!positive(spot) || !positive(leg.strike) || T <= 0) return null;

  let iv: number | null = null;
  let source: VolSource | null = null;
  if (positive(pd?.impliedVol)) {
    iv = pd!.impliedVol!;
    source = "stream";
  } else {
    const observed = observedPrice(pd);
    if (observed) {
      const volSpot = observed.source === "close" && positive(tickerPd?.close) ? tickerPd!.close! : spot;
      const volT = observed.source === "close" ? T + 1 / 365 : T;
      iv = bsImpliedVol(observed.price, volSpot, leg.strike, volT, r, leg.right === "C" ? "Call" : "Put");
      source = iv == null ? null : observed.source;
    }
  }
  if (!positive(iv) || source == null) return null;

  const computedDelta = optionDelta(leg.right, spot, leg.strike, T, r, iv);
  const providerDelta = validProviderDelta(pd?.delta) ? pd!.delta! : null;
  const canonicalProviderDelta =
    providerDelta == null
      ? null
      : leg.right === "P" && providerDelta > 0
        ? providerDelta - 1
        : providerDelta;
  const providerDeltaIsCanonical =
    canonicalProviderDelta != null &&
    (leg.right === "C"
      ? canonicalProviderDelta >= 0 - DELTA_EPS && canonicalProviderDelta <= 1 + DELTA_EPS
      : canonicalProviderDelta >= -1 - DELTA_EPS && canonicalProviderDelta <= 0 + DELTA_EPS);
  const delta =
    source === "stream" && providerDeltaIsCanonical
      ? canonicalProviderDelta
      : computedDelta;

  if (!validProviderDelta(delta)) return null;

  return {
    strike: leg.strike,
    iv,
    delta,
    source,
  };
}

export function computeShortStrangleSkew(input: ShortStrangleSkewInput): ShortStrangleSkewResult | null {
  if (detectStructure(input.legs) !== "Short Strangle") return null;

  const normalized = normalizeComboOrder(input.legs);
  const callLeg = normalized.legs.find((leg) => leg.right === "C" && leg.action === "SELL");
  const putLeg = normalized.legs.find((leg) => leg.right === "P" && leg.action === "SELL");
  if (!callLeg || !putLeg || callLeg.expiry !== putLeg.expiry || callLeg.strike === putLeg.strike) return null;

  const expiry = callLeg.expiry;
  const spot = resolveSpot(input, expiry);
  if (!spot) return { status: "unavailable", reason: "missing-spot" };

  const now = input.now ?? new Date();
  const T = yearsToExpiry(expiry, now);
  if (T == null || T <= 0) return { status: "unavailable", reason: "missing-vol" };
  const r = input.riskFreeRate ?? RISK_FREE_RATE_DEFAULT;

  const callPd = input.prices[optionKey({ symbol: input.ticker, expiry: callLeg.expiry, strike: callLeg.strike, right: "C" })];
  const putPd = input.prices[optionKey({ symbol: input.ticker, expiry: putLeg.expiry, strike: putLeg.strike, right: "P" })];
  const tickerPd = input.prices[input.ticker.toUpperCase()];
  const call = resolveLeg(input.ticker, callLeg, callPd, tickerPd, spot, T, r);
  const put = resolveLeg(input.ticker, putLeg, putPd, tickerPd, spot, T, r);
  if (!call || !put) return { status: "unavailable", reason: "missing-vol" };

  const quantity = normalized.quantity;
  const skewVolPts = (put.iv - call.iv) * 100;
  const netDeltaPerCombo = (-call.delta * callLeg.quantity) + (-put.delta * putLeg.quantity);
  const deltaSharesPerCombo = netDeltaPerCombo * 100;

  return {
    status: "ready",
    call,
    put,
    spot,
    expiry,
    quantity,
    skewVolPts,
    skewSide: Math.abs(skewVolPts) < 0.05 ? "FLAT" : skewVolPts > 0 ? "PUT" : "CALL",
    netDeltaPerCombo,
    deltaSharesPerCombo,
    deltaSharesTotal: deltaSharesPerCombo * quantity,
  };
}
