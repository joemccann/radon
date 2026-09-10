import type {
  OptionsExposureFrequency,
  OptionsExposureLevel,
  OptionsExposurePayload,
} from "@/lib/optionsExposure";
import { demoBasePrice, demoSymbolHash, roundDemoPrice } from "./market";

export type DemoOptionExpirationsPayload = {
  symbol: string;
  expirations: string[];
};

export type DemoOptionChainPayload = DemoOptionExpirationsPayload & {
  expiry: string;
  exchange: "SMART";
  strikes: number[];
  multiplier: "100";
};

const DAY_MS = 86_400_000;

function utcDay(date: Date): Date {
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
}

function compactDate(date: Date): string {
  return date.toISOString().slice(0, 10).replaceAll("-", "");
}

function isoDate(compact: string): string {
  return `${compact.slice(0, 4)}-${compact.slice(4, 6)}-${compact.slice(6, 8)}`;
}

/**
 * A small, rolling calendar prevents the sample chain from expiring while
 * keeping demo navigation bounded. Four weeklies plus three standard monthly
 * expiries are enough to exercise every chain control without background fanout.
 */
export function buildDemoOptionExpirations(
  symbol: string,
  now: Date = new Date(),
): DemoOptionExpirationsPayload {
  const weeklies: string[] = [];
  const monthlies: string[] = [];
  const cursor = utcDay(now);

  for (let offset = 1; offset <= 140 && (weeklies.length < 4 || monthlies.length < 3); offset += 1) {
    const candidate = new Date(cursor.getTime() + offset * DAY_MS);
    if (candidate.getUTCDay() !== 5) continue;
    const compact = compactDate(candidate);
    if (weeklies.length < 4) weeklies.push(compact);
    const dayOfMonth = candidate.getUTCDate();
    if (dayOfMonth >= 15 && dayOfMonth <= 21 && monthlies.length < 3) {
      monthlies.push(compact);
    }
  }

  return {
    symbol: symbol.trim().toUpperCase(),
    expirations: [...new Set([...weeklies, ...monthlies])].sort().slice(0, 7),
  };
}

function strikeStep(spot: number): number {
  if (spot >= 200) return 5;
  if (spot >= 75) return 2.5;
  if (spot >= 30) return 1;
  return 0.5;
}

function demoStrikes(symbol: string, width = 30): number[] {
  const spot = demoBasePrice(symbol);
  const step = strikeStep(spot);
  const center = Math.round(spot / step) * step;
  return Array.from(
    { length: width * 2 + 1 },
    (_, index) => roundDemoPrice(center + (index - width) * step),
  ).filter((strike) => strike > 0);
}

export function buildDemoOptionChain(
  symbol: string,
  requestedExpiry?: string | null,
  now: Date = new Date(),
): DemoOptionChainPayload {
  const calendar = buildDemoOptionExpirations(symbol, now);
  const normalizedExpiry = requestedExpiry?.replaceAll("-", "") ?? calendar.expirations[0];
  const expirations = calendar.expirations.includes(normalizedExpiry)
    ? calendar.expirations
    : [...calendar.expirations, normalizedExpiry].sort();

  return {
    ...calendar,
    expirations,
    expiry: normalizedExpiry,
    exchange: "SMART",
    strikes: demoStrikes(calendar.symbol),
    multiplier: "100",
  };
}

function roundedMeasurement(value: number): number {
  return Math.round(value * 100) / 100;
}

export function buildDemoOptionsExposure(
  symbol: string,
  frequency: OptionsExposureFrequency,
  now: Date = new Date(),
): OptionsExposurePayload {
  const normalizedSymbol = symbol.trim().toUpperCase();
  const spot = demoBasePrice(normalizedSymbol);
  const allStrikes = demoStrikes(normalizedSymbol, 10);
  const compactExpirations = buildDemoOptionExpirations(normalizedSymbol, now).expirations.slice(0, 3);
  const today = utcDay(now).getTime();
  const expirations = compactExpirations.map((compact) => {
    const expirationDate = isoDate(compact);
    const expirationMs = Date.parse(`${expirationDate}T00:00:00.000Z`);
    return {
      expiration_date: expirationDate,
      dte: Math.max(1, Math.ceil((expirationMs - today) / DAY_MS)),
    };
  });
  const cells: OptionsExposurePayload["cells"] = {
    net_gex: [],
    abs_gex: [],
    net_dex: [],
    abs_dex: [],
    oi_call: [],
    oi_put: [],
    strike_idx: [],
    expiration_idx: [],
  };
  const seed = demoSymbolHash(normalizedSymbol);
  const step = strikeStep(spot);

  for (let expirationIndex = 0; expirationIndex < expirations.length; expirationIndex += 1) {
    for (let strikeIndex = 0; strikeIndex < allStrikes.length; strikeIndex += 1) {
      const strike = allStrikes[strikeIndex];
      const distance = (strike - spot) / step;
      const decay = 1 / (1 + expirationIndex * 0.35);
      const callOi = 180 + ((seed + strikeIndex * 47 + expirationIndex * 89) % 620);
      const putOi = 160 + ((seed + strikeIndex * 71 + expirationIndex * 53) % 680);
      const gammaShape = Math.exp(-Math.abs(distance) / 5) * decay;
      const signedGamma = (callOi - putOi) * spot * gammaShape * 32;
      const absoluteGamma = (callOi + putOi) * spot * gammaShape * 32;
      const signedDelta = (callOi * 0.55 - putOi * 0.45) * spot * 100 * decay;
      const absoluteDelta = (callOi * 0.55 + putOi * 0.45) * spot * 100 * decay;

      cells.strike_idx.push(strikeIndex);
      cells.expiration_idx.push(expirationIndex);
      cells.net_gex.push(roundedMeasurement(signedGamma));
      cells.abs_gex.push(roundedMeasurement(absoluteGamma));
      cells.net_dex.push(roundedMeasurement(signedDelta));
      cells.abs_dex.push(roundedMeasurement(absoluteDelta));
      cells.oi_call.push(callOi);
      cells.oi_put.push(putOi);
    }
  }

  const center = Math.round(spot / step) * step;
  const levels: OptionsExposureLevel[] = [
    { key: "hvl", label: "HVL", value: center },
    { key: "call_resistance", label: "Call resistance", value: center + step * 4 },
    { key: "put_support", label: "Put support", value: center - step * 4 },
    { key: "call_resistance_0dte", label: "Call resistance 0DTE", value: center + step * 2 },
    { key: "put_support_0dte", label: "Put support 0DTE", value: center - step * 2 },
    { key: "max_1d", label: "1D max", value: center + step * 3 },
    { key: "min_1d", label: "1D min", value: center - step * 3 },
  ];
  const timestamp = now.toISOString();

  return {
    schema_version: 1,
    symbol: normalizedSymbol,
    source: "radon_demo_fixture",
    source_time: timestamp,
    fetched_at: timestamp,
    frequency,
    spot,
    strikes: allStrikes,
    expirations,
    cells,
    levels,
    units: {
      net_gex: "USD per 1% move",
      abs_gex: "USD per 1% move",
      net_dex: "USD delta",
      abs_dex: "USD delta",
      open_interest: "contracts",
    },
    complete: true,
  };
}
