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
  const expiry = searchParams.get("expiry");

  if (!symbol) {
    return optionsJson({ error: "Required: symbol", code: "BAD_REQUEST" }, 400);
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
