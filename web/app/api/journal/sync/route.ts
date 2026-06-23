import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { importLatestReconciliationToJournal } from "@/lib/journalDb";

export const runtime = "nodejs";

/**
 * POST /api/journal/sync
 *
 * Runs IB reconciliation, imports new IB trades into the Turso journal table.
 * Returns { imported, skipped } counts.
 */
export async function POST(): Promise<Response> {
  try {
    await radonFetch("/journal/reconcile", { method: "POST", timeout: 130_000 });
    const result = await importLatestReconciliationToJournal();
    return NextResponse.json({
      imported: result.imported,
      skipped: result.skipped,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Sync failed";
    return NextResponse.json({ error: message, imported: 0, skipped: 0 }, { status: 500 });
  }
}
