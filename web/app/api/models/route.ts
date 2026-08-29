import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { resolveModelCatalog } from "@/lib/llm/catalog";

// Disable Next.js static caching: this handler reports which provider keys are
// present in the running process. Freezing the first response would keep the
// picker showing yesterday's provider set after a key is added and the unit
// restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

export const radonCapability = "read";

/**
 * GET /api/models — the chat model picker's source of truth.
 *
 * Returns { models, defaultId, source }. Providers without a key in this
 * deployment are absent from `models`; no key material is read, logged, or
 * serialized anywhere on this path. No-store: the answer is per-deployment
 * process state, never a shareable cache entry.
 */
export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const catalog = await resolveModelCatalog();
  return setNoStoreResponseHeaders(NextResponse.json(catalog), requestId);
}
