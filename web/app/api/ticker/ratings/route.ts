import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { scrubSecrets } from "@/lib/apiContracts";

export const runtime = "nodejs";

export async function GET(request: Request): Promise<Response> {
  const { searchParams } = new URL(request.url);
  const ticker = searchParams.get("ticker");

  if (!ticker) {
    return NextResponse.json({ error: "ticker parameter required" }, { status: 400 });
  }

  try {
    const data = await radonFetch<Record<string, unknown>>(
      `/ticker/ratings?ticker=${encodeURIComponent(ticker.toUpperCase())}`,
      { timeout: 60_000 },
    );
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
