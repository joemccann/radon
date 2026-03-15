import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { radonFetch } from "@/lib/radonApi";

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
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
