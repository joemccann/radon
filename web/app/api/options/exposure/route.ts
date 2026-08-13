import { requireRouteAccess } from "@/lib/routeAccess";

import { radonFetch } from "@/lib/radonApi";
import {
  OPTIONS_PROXY_TIMEOUT_MS,
  optionsErrorResponse,
  optionsJson,
} from "../_shared";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const SYMBOL_RE = /^[A-Z][A-Z0-9.-]{0,9}$/;
const FREQUENCIES = new Set(["eod", "intraday"]);

export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "options/exposure:route", limit: 20, windowMs: 60_000 } });
  if (!access.ok) return access.response;
  const { searchParams } = new URL(request.url);
  const rawSymbol = searchParams.get("symbol");
  const symbol = rawSymbol?.toUpperCase();
  const frequency = searchParams.get("frequency") ?? "eod";

  if (!rawSymbol) {
    return optionsJson({ error: "Required: symbol", code: "BAD_REQUEST" }, 400);
  }
  if (!symbol || !SYMBOL_RE.test(symbol)) {
    return optionsJson({ error: "Invalid symbol", code: "BAD_REQUEST" }, 400);
  }
  if (!FREQUENCIES.has(frequency)) {
    return optionsJson({ error: "Invalid frequency", code: "BAD_REQUEST" }, 400);
  }

  try {
    const data = await radonFetch<Record<string, unknown>>(
      `/options/exposure/${symbol}?frequency=${frequency}`,
      { timeout: OPTIONS_PROXY_TIMEOUT_MS },
    );
    return optionsJson(data);
  } catch (error) {
    return optionsErrorResponse("Options exposure unavailable", error);
  }
}
