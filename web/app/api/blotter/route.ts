import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { radonFetch } from "@/lib/radonApi";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const BLOTTER_CACHE_PATH = join(process.cwd(), "..", "data", "blotter.json");

export async function GET(): Promise<Response> {
  try {
    const raw = await readFile(BLOTTER_CACHE_PATH, "utf-8");
    const data = JSON.parse(raw);
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({
      as_of: "",
      summary: { closed_trades: 0, open_trades: 0, total_commissions: 0, realized_pnl: 0 },
      closed_trades: [],
      open_trades: [],
    });
  }
}

export async function POST(): Promise<Response> {
  try {
    const data = await radonFetch("/blotter", { method: "POST", timeout: 130_000 });
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Blotter sync failed";
    try {
      const cached = await readFile(BLOTTER_CACHE_PATH, "utf-8");
      const parsed = JSON.parse(cached);
      const res = NextResponse.json(parsed);
      res.headers.set("X-Sync-Warning", `Blotter sync failed - serving cached data (${message})`);
      return res;
    } catch {
      return NextResponse.json({ error: message }, { status: 502 });
    }
  }
}
