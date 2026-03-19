import { NextResponse } from "next/server";

export const runtime = "nodejs";

const FASTAPI_BASE = process.env.FASTAPI_URL ?? "http://localhost:8321";

export async function POST(): Promise<Response> {
  try {
    const upstream = await fetch(`${FASTAPI_BASE}/regime/share`, { method: "POST" });
    const data = await upstream.json() as Record<string, unknown>;
    if (!upstream.ok) {
      const detail = typeof data?.detail === "string" ? data.detail : "Share generation failed";
      return NextResponse.json({ error: detail }, { status: upstream.status });
    }
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 }
    );
  }
}
