import { NextResponse } from "next/server";
import { runJournalSync } from "@/lib/journalSync";
import { radonFetch } from "@/lib/radonApi";

export const runtime = "nodejs";

/**
 * POST /api/journal/sync
 *
 * Reads reconciliation.json, imports new IB trades into trade_log.json.
 * Returns { imported, skipped } counts.
 */
export async function POST(): Promise<Response> {
  try {
    await radonFetch("/journal/reconcile", { method: "POST", timeout: 130_000 });
    const result = await runJournalSync();
    return NextResponse.json({
      imported: result.imported,
      skipped: result.skipped,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Sync failed";
    return NextResponse.json({ error: message, imported: 0, skipped: 0 }, { status: 500 });
  }
}
