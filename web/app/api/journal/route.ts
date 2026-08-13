import { requireRouteAccess } from "@/lib/routeAccess";
import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import {
  importLatestReconciliationToJournal,
  readJournalFromDb,
} from "@/lib/journalDb";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

// Disable Next.js static caching: this handler reads live Turso state.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "journal:route", limit: 20, windowMs: 60_000 } });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  try {
    const data = await readJournalFromDb();
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch {
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: "Journal temporarily unavailable", trades: [] }, { status: 500 }),
      requestId,
    );
  }
}

export async function POST(): Promise<Response> {
  const access = await requireRouteAccess(undefined, {
    operatorOnly: true,
    rate: { key: "journal-reconcile", limit: 2, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  try {
    await radonFetch("/journal/reconcile", { method: "POST", timeout: 130_000 });
    await importLatestReconciliationToJournal();
    const data = await readJournalFromDb();
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch {
    try {
      const cached = await readJournalFromDb();
      const response = NextResponse.json(cached);
      response.headers.set("X-Sync-Warning", "Journal sync failed - serving cached data");
      return setNoStoreResponseHeaders(response, requestId);
    } catch {
      return setNoStoreResponseHeaders(
        NextResponse.json({ error: "Journal sync failed", trades: [] }, { status: 500 }),
        requestId,
      );
    }
  }
}
