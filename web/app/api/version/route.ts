import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { runningRelease } from "@/lib/releaseStatus";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = "internal";

/** The identity baked into this deploy. Cache must not keep a previous SHA. */
export function GET() {
  return setNoStoreResponseHeaders(NextResponse.json(runningRelease()), getRequestId());
}
