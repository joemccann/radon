import { NextResponse } from "next/server";
import { readFile, stat } from "fs/promises";
import { join } from "path";
import { radonFetch } from "@/lib/radonApi";
import { runJournalSync } from "@/lib/journalSync";
import { getDb } from "@/lib/db";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const DATA_DIR = join(process.cwd(), "..", "data");
const TRADE_LOG_PATH = join(DATA_DIR, "trade_log.json");
const RECONCILIATION_PATH = join(DATA_DIR, "reconciliation.json");
const CACHE_TTL_MS = 60_000;

let bgSyncInFlight = false;

type JournalPayload = {
  trades: unknown[];
  error?: string;
};

type ReconciliationPayload = {
  timestamp?: string;
  needs_attention?: boolean;
  new_trades?: unknown[];
};

async function readJournalFromDb(): Promise<JournalPayload | null> {
  try {
    const db = getDb();
    const result = await db.execute({
      sql: `SELECT payload FROM journal ORDER BY filled_at DESC, written_at DESC LIMIT 5000`,
      args: [],
    });
    if (result.rows.length === 0) return null;
    const trades = result.rows.map((r) =>
      JSON.parse((r as unknown as { payload: string }).payload),
    );
    return { trades };
  } catch {
    return null;
  }
}

async function readJournalFromDisk(): Promise<JournalPayload | null> {
  try {
    const raw = await readFile(TRADE_LOG_PATH, "utf-8");
    return JSON.parse(raw) as JournalPayload;
  } catch {
    return null;
  }
}

function latestFilledAt(payload: JournalPayload | null): string {
  if (!payload?.trades?.length) return "";
  let max = "";
  for (const t of payload.trades as Array<Record<string, unknown>>) {
    const candidate =
      (t.filled_at as string | undefined) ??
      (t.date as string | undefined) ??
      (t.close_date as string | undefined) ??
      "";
    if (candidate > max) max = candidate;
  }
  return max;
}

async function readJournal(): Promise<JournalPayload> {
  // Prefer whichever source has the most-recent filled_at. This protects
  // against silent staleness when one source drifts: a half-empty DB
  // shouldn't mask a fresher disk file (or vice versa). When both are
  // current, the DB wins on the tie (cheaper read on the embedded replica).
  const [fromDb, fromDisk] = await Promise.all([
    readJournalFromDb(),
    readJournalFromDisk(),
  ]);
  if (!fromDb && !fromDisk) {
    throw new Error("Journal unavailable: both DB and disk reads failed");
  }
  if (!fromDb) return fromDisk!;
  if (!fromDisk) return fromDb;
  return latestFilledAt(fromDisk) > latestFilledAt(fromDb) ? fromDisk : fromDb;
}

async function readReconciliation(): Promise<ReconciliationPayload | null> {
  try {
    const raw = await readFile(RECONCILIATION_PATH, "utf-8");
    return JSON.parse(raw) as ReconciliationPayload;
  } catch {
    return null;
  }
}

async function isJournalStale(): Promise<boolean> {
  const recon = await readReconciliation();
  if (!recon) return true;
  if (recon.needs_attention) return true;

  try {
    const s = await stat(TRADE_LOG_PATH);
    return Date.now() - s.mtimeMs > CACHE_TTL_MS;
  } catch {
    return true;
  }
}

function triggerBackgroundSync(): void {
  if (bgSyncInFlight) return;
  bgSyncInFlight = true;

  radonFetch("/journal/reconcile", { method: "POST", timeout: 130_000 })
    .then(async () => {
      await runJournalSync();
    })
    .catch((err) => {
      console.warn("[Journal] Background sync failed:", err instanceof Error ? err.message : String(err));
    })
    .finally(() => {
      bgSyncInFlight = false;
    });
}

export async function GET(): Promise<Response> {
  try {
    if (await isJournalStale()) {
      triggerBackgroundSync();
    }

    const data = await readJournal();
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to read trade log";
    return NextResponse.json({ error: message, trades: [] }, { status: 500 });
  }
}

export async function POST(): Promise<Response> {
  try {
    await radonFetch("/journal/reconcile", { method: "POST", timeout: 130_000 });
    await runJournalSync();
    const data = await readJournal();
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Journal sync failed";
    try {
      const cached = await readJournal();
      const response = NextResponse.json(cached);
      response.headers.set("X-Sync-Warning", `Journal sync failed: ${message}`);
      return response;
    } catch {
      return NextResponse.json({ error: message, trades: [] }, { status: 500 });
    }
  }
}
