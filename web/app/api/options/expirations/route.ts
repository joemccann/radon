import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";

export const runtime = "nodejs";

export async function GET(request: Request): Promise<Response> {
  const { searchParams } = new URL(request.url);
  const symbol = searchParams.get("symbol")?.toUpperCase();

  if (!symbol) {
    return NextResponse.json({ error: "Required: symbol" }, { status: 400 });
  }

  try {
    const data = await radonFetch<Record<string, unknown>>(
      `/options/expirations?symbol=${symbol}`,
      { timeout: 20_000 },
    );

    return NextResponse.json({
      symbol: data.symbol,
      expirations: data.expirations,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to fetch expirations";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
