import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { radonFetch, RadonApiError } from "@/lib/radonApi";

export const runtime = "nodejs";

export async function POST(): Promise<Response> {
  try {
    const { getToken } = await auth();
    const token = await getToken() ?? undefined;
    const data = await radonFetch("/gex/share", { method: "POST", token });
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
