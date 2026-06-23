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
  const requestId = getRequestId();
  try {
    const data = await readJournalFromDb();
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to read journal";
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message, trades: [] }, { status: 500 }),
      requestId,
    );
  }
}

export async function POST(): Promise<Response> {
  const requestId = getRequestId();
  try {
    await radonFetch("/journal/reconcile", { method: "POST", timeout: 130_000 });
    await importLatestReconciliationToJournal();
    const data = await readJournalFromDb();
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Journal sync failed";
    try {
      const cached = await readJournalFromDb();
      const response = NextResponse.json(cached);
      response.headers.set("X-Sync-Warning", `Journal sync failed: ${message}`);
      return setNoStoreResponseHeaders(response, requestId);
    } catch {
      return setNoStoreResponseHeaders(
        NextResponse.json({ error: message, trades: [] }, { status: 500 }),
        requestId,
      );
    }
  }
}
