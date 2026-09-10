import { requireRouteAccess } from "@/lib/routeAccess";

import { radonFetch } from "@/lib/radonApi";
import {
  OPTIONS_PROXY_TIMEOUT_MS,
  optionsErrorResponse,
  optionsJson,
} from "../_shared";
import { boundedTicker } from "@/lib/requestBounds";
import { buildDemoOptionExpirations } from "@/lib/demo/fixtures/options";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export const radonCapability = "read";

export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, {
    rate: { key: "options/expirations:route", limit: 20, windowMs: 60_000 },
    durableRateTier: "A",
  });
  if (!access.ok) return access.response;
  const { searchParams } = new URL(request.url);
  const symbol = boundedTicker(searchParams.get("symbol"));

  if (!symbol) {
    return optionsJson({ error: "Required: symbol", code: "BAD_REQUEST" }, 400);
  }

  if (access.principal.kind === "demo") {
    return optionsJson({ ...buildDemoOptionExpirations(symbol) });
  }

  try {
    const data = await radonFetch<Record<string, unknown>>(
      `/options/expirations?symbol=${encodeURIComponent(symbol)}`,
      { timeout: OPTIONS_PROXY_TIMEOUT_MS },
    );

    return optionsJson({
      symbol: data.symbol,
      expirations: data.expirations,
    });
  } catch (error) {
    return optionsErrorResponse("Option expirations unavailable", error);
  }
}
