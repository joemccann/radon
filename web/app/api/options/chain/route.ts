import { requireRouteAccess } from "@/lib/routeAccess";

import { radonFetch } from "@/lib/radonApi";
import {
  OPTIONS_PROXY_TIMEOUT_MS,
  optionsErrorResponse,
  optionsJson,
} from "../_shared";
import { boundedTicker, OPTION_EXPIRY_PATTERN } from "@/lib/requestBounds";
import { buildDemoOptionChain } from "@/lib/demo/fixtures/options";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export const radonCapability = "read";

export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, {
    rate: { key: "options/chain:route", limit: 20, windowMs: 60_000 },
    durableRateTier: "A",
  });
  if (!access.ok) return access.response;
  const { searchParams } = new URL(request.url);
  const symbol = boundedTicker(searchParams.get("symbol"));
  const expiry = searchParams.get("expiry");

  if (!symbol) {
    return optionsJson({ error: "Required: symbol", code: "BAD_REQUEST" }, 400);
  }
  if (expiry && !OPTION_EXPIRY_PATTERN.test(expiry)) {
    return optionsJson({ error: "Invalid expiry", code: "BAD_REQUEST" }, 400);
  }

  if (access.principal.kind === "demo") {
    return optionsJson({ ...buildDemoOptionChain(symbol, expiry) });
  }

  try {
    const params = new URLSearchParams({ symbol });
    if (expiry) params.set("expiry", expiry);

    const data = await radonFetch<Record<string, unknown>>(
      `/options/chain?${params}`,
      { timeout: OPTIONS_PROXY_TIMEOUT_MS },
    );

    return optionsJson(data);
  } catch (error) {
    return optionsErrorResponse("Option chain unavailable", error);
  }
}
