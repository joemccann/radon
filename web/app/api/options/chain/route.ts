import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";

export const runtime = "nodejs";

export async function GET(request: Request): Promise<Response> {
  const { searchParams } = new URL(request.url);
  const symbol = searchParams.get("symbol")?.toUpperCase();
  const expiry = searchParams.get("expiry");

  if (!symbol) {
    return NextResponse.json({ error: "Required: symbol" }, { status: 400 });
  }

  try {
    const params = new URLSearchParams({ symbol });
    if (expiry) params.set("expiry", expiry);

    const data = await radonFetch<Record<string, unknown>>(
      `/options/chain?${params}`,
      { timeout: 20_000 },
    );

    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to fetch option chain";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
