import { requireRouteAccess } from "@/lib/routeAccess";
import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { importReconciliationSnapshotToJournal } from "@/lib/journalDb";

export const runtime = "nodejs";

/**
 * POST /api/journal/sync
 *
 * Runs IB reconciliation, imports new IB trades into the Turso journal table.
 * Returns { imported, skipped } counts.
 */
export async function POST(): Promise<Response> {
  const access = await requireRouteAccess(undefined, {
    operatorOnly: true,
    rate: { key: "journal-sync", limit: 2, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;
  try {
    const requestedAt = Date.now();
    const reconciliation = await radonFetch<{ snapshot_at?: unknown }>("/journal/reconcile", {
      method: "POST",
      timeout: 130_000,
    });
    const snapshotAt = typeof reconciliation.snapshot_at === "string"
      ? reconciliation.snapshot_at
      : "";
    const snapshotMs = Date.parse(snapshotAt);
    if (!snapshotAt || !Number.isFinite(snapshotMs) || snapshotMs < requestedAt - 1_000) {
      throw new Error("Reconciliation returned a stale snapshot");
    }
    const result = await importReconciliationSnapshotToJournal(snapshotAt);
    return NextResponse.json({
      imported: result.imported,
      skipped: result.skipped,
    });
  } catch {
    return NextResponse.json({ error: "Journal sync failed", imported: 0, skipped: 0 }, { status: 500 });
  }
}
