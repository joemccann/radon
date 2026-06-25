import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

export async function POST(request?: Request) {
  const requestId = getRequestId();
  try {
    const authHeader = request?.headers.get("authorization") ?? "";
    const token = authHeader.toLowerCase().startsWith("bearer ")
      ? authHeader.slice("bearer ".length).trim()
      : undefined;
    const data = await radonFetch<{ ticket: string }>("/ws-ticket", {
      method: "POST",
      token,
    });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (err) {
    const status = (err as { status?: number }).status ?? 500;
    const detail = err instanceof Error ? err.message : "WS ticket failed";
    return setNoStoreResponseHeaders(NextResponse.json({ detail }, { status }), requestId);
  }
}
