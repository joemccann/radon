import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { scrubSecrets } from "@/lib/apiContracts";
import { boundedTicker } from "@/lib/requestBounds";

export const runtime = "nodejs";

const ratingsInFlight = new Map<string, Promise<Record<string, unknown>>>();

function coalescedRatings(ticker: string): Promise<Record<string, unknown>> {
  const existing = ratingsInFlight.get(ticker);
  if (existing) return existing;
  const pending = radonFetch<Record<string, unknown>>(
    `/ticker/ratings?ticker=${encodeURIComponent(ticker)}`,
    { timeout: 60_000 },
  ).finally(() => {
    if (ratingsInFlight.get(ticker) === pending) ratingsInFlight.delete(ticker);
  });
  ratingsInFlight.set(ticker, pending);
  return pending;
}

export const radonCapability = "read";

export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "ticker/ratings:route", limit: 20, windowMs: 60_000 }, durableRateTier: "A" });
  if (!access.ok) return access.response;
  const { searchParams } = new URL(request.url);
  const ticker = boundedTicker(searchParams.get("ticker"));

  if (!ticker) {
    return NextResponse.json({ error: "ticker parameter required" }, { status: 400 });
  }

  try {
    const data = await coalescedRatings(ticker);
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to fetch ratings";
    return NextResponse.json(
      // Scrub the raw upstream error before it reaches the client — a LibsqlError
      // carries the Turso URL/token. This route builds its body inline rather
      // than via jsonApiError, so it must scrub explicitly.
      { error: "Failed to fetch ratings", detail: scrubSecrets(message) },
      { status: 502 },
    );
  }
}
