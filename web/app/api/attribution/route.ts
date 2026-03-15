import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";

export const runtime = "nodejs";

export async function GET() {
  try {
    const data = await radonFetch("/attribution", { timeout: 20_000 });
    return NextResponse.json(data);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
