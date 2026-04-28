import { NextResponse } from "next/server";

/**
 * Returns the current effective Fed Funds rate (FRED series DFF) as a
 * decimal (e.g. 0.0364 for 3.64%). FRED publishes a free CSV without
 * authentication. We pull the last 30 days and pick the latest non-stale row.
 *
 * Cached 24h via Next.js fetch revalidation. On any failure the route
 * returns rate=0 with `stale: true` so callers can fall back to a 0
 * risk-free rate without throwing.
 */

const FRED_DFF_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF";
const REVALIDATE_SECONDS = 86_400;

type RiskFreeRateResponse = {
  rate: number;
  asOf: string | null;
  source: "FRED:DFF" | "fallback";
  stale: boolean;
};

async function fetchLatestDff(): Promise<{ rate: number; asOf: string } | null> {
  const since = new Date();
  since.setUTCDate(since.getUTCDate() - 30);
  const cosd = since.toISOString().slice(0, 10);

  const res = await fetch(`${FRED_DFF_URL}&cosd=${cosd}`, {
    next: { revalidate: REVALIDATE_SECONDS },
  });
  if (!res.ok) return null;

  const csv = await res.text();
  const lines = csv.trim().split(/\r?\n/);
  for (let i = lines.length - 1; i >= 1; i--) {
    const [date, value] = lines[i].split(",");
    if (!date || !value) continue;
    const parsed = Number.parseFloat(value);
    if (!Number.isFinite(parsed)) continue;
    return { rate: parsed / 100, asOf: date };
  }
  return null;
}

export async function GET() {
  try {
    const result = await fetchLatestDff();
    if (!result) {
      return NextResponse.json<RiskFreeRateResponse>(
        { rate: 0, asOf: null, source: "fallback", stale: true },
        { headers: { "Cache-Control": "public, max-age=300" } },
      );
    }
    return NextResponse.json<RiskFreeRateResponse>(
      { rate: result.rate, asOf: result.asOf, source: "FRED:DFF", stale: false },
      { headers: { "Cache-Control": `public, max-age=${REVALIDATE_SECONDS}` } },
    );
  } catch {
    return NextResponse.json<RiskFreeRateResponse>(
      { rate: 0, asOf: null, source: "fallback", stale: true },
      { headers: { "Cache-Control": "public, max-age=300" } },
    );
  }
}
