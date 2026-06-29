import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import { rateLimit, clientIp, SHARE_CARD_LIMIT, SHARE_WINDOW_MS } from "@/lib/rateLimit";

export const runtime = "nodejs";

export async function POST(request: Request): Promise<Response> {
  const limit = rateLimit(clientIp(request), { limit: SHARE_CARD_LIMIT, windowMs: SHARE_WINDOW_MS });
  if (!limit.ok) {
    return NextResponse.json(
      { error: "Too Many Requests" },
      { status: 429, headers: { "Retry-After": String(limit.retryAfterSec) } },
    );
  }

  try {
    const { getToken } = await auth();
    const token = await getToken() ?? undefined;
    const data = await radonFetch("/cta/share", { method: "POST", token });
    return NextResponse.json(data);
  } catch (err) {
    if (err instanceof RadonApiError) {
      return NextResponse.json({ error: err.detail }, { status: err.status });
    }
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 },
    );
  }
}
