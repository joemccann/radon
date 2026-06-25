import { radonFetch } from "@/lib/radonApi";
import {
  OPTIONS_PROXY_TIMEOUT_MS,
  optionsErrorResponse,
  optionsJson,
} from "../_shared";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(request: Request): Promise<Response> {
  const { searchParams } = new URL(request.url);
  const symbol = searchParams.get("symbol")?.toUpperCase();

  if (!symbol) {
    return optionsJson({ error: "Required: symbol", code: "BAD_REQUEST" }, 400);
  }

  try {
    const data = await radonFetch<Record<string, unknown>>(
      `/options/expirations?symbol=${symbol}`,
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
