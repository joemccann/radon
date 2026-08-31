import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { scrubSecrets } from "@/lib/apiContracts";
import { boundedTicker } from "@/lib/requestBounds";

export const runtime = "nodejs";

/**
 * Worst-case upstream walk: IB pool (bounded 15s) -> UW -> Robinhood ->
 * Yahoo, each with its own network budget. 60s covers the full ladder.
 */
const STREAKS_TIMEOUT_MS = 60_000;

const streaksInFlight = new Map<string, Promise<Record<string, unknown>>>();

function coalescedStreaks(symbol: string): Promise<Record<string, unknown>> {
  const existing = streaksInFlight.get(symbol);
  if (existing) return existing;
  const pending = radonFetch<Record<string, unknown>>(
    `/streaks/${encodeURIComponent(symbol)}`,
    { timeout: STREAKS_TIMEOUT_MS },
  ).finally(() => {
    if (streaksInFlight.get(symbol) === pending) streaksInFlight.delete(symbol);
  });
  streaksInFlight.set(symbol, pending);
  return pending;
}

export const radonCapability = "read";

export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, {
    rate: { key: "streaks:route", limit: 20, windowMs: 60_000 },
  });
  if (!access.ok) return access.response;

  const { searchParams } = new URL(request.url);
  const symbol = boundedTicker(searchParams.get("symbol"));
  if (!symbol) {
    return NextResponse.json({ error: "symbol parameter required" }, { status: 400 });
  }

  try {
    const data = await coalescedStreaks(symbol);
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to fetch streaks";
    return NextResponse.json(
      // Scrub the raw upstream error before it reaches the client.
      { error: "Failed to fetch streaks", detail: scrubSecrets(message) },
      { status: 502 },
    );
  }
}
