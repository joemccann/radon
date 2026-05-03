import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { radonFetch } from "@/lib/radonApi";
import { getDb } from "@/lib/db";
import { journalRowsToBlotter, type JournalRow } from "@/lib/blotter/fromJournal";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const BLOTTER_CACHE_PATH = join(process.cwd(), "..", "data", "blotter.json");

async function readBlotterFromJournal(): Promise<unknown | null> {
  // Single source of truth: the Turso `journal` table. Same data the
  // /journal page reads, projected into the historical-trades shape.
  // When the table is empty we fall through to the legacy
  // blotter.json mirror so deploys with an unbootstrapped DB still work.
  try {
    const db = getDb();
    const result = await db.execute({
      sql: `SELECT payload, filled_at FROM journal ORDER BY filled_at DESC LIMIT 5000`,
      args: [],
    });
    if (result.rows.length === 0) return null;
    const rows: JournalRow[] = result.rows.map((r) => {
      const row = r as unknown as { payload: string; filled_at: string | null };
      return {
        payload: JSON.parse(row.payload),
        filled_at: row.filled_at,
      };
    });
    return journalRowsToBlotter(rows);
  } catch {
    return null;
  }
}

async function readBlotterFromDisk(): Promise<unknown | null> {
  try {
    const raw = await readFile(BLOTTER_CACHE_PATH, "utf-8");
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export async function GET(): Promise<Response> {
  const fromJournal = await readBlotterFromJournal();
  if (fromJournal) return NextResponse.json(fromJournal);

  const fromDisk = await readBlotterFromDisk();
  if (fromDisk) return NextResponse.json(fromDisk);

  return NextResponse.json({
    as_of: "",
    summary: { closed_trades: 0, open_trades: 0, total_commissions: 0, realized_pnl: 0 },
    closed_trades: [],
    open_trades: [],
  });
}

export async function POST(): Promise<Response> {
  // POST still kicks the legacy Flex Query path so the on-disk mirror
  // and the blotter_service cache stay current. Once the journal table
  // is the only consumer this can be retired.
  try {
    const data = await radonFetch("/blotter", { method: "POST", timeout: 130_000 });
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Blotter sync failed";
    const fromJournal = await readBlotterFromJournal();
    if (fromJournal) {
      const res = NextResponse.json(fromJournal);
      res.headers.set("X-Sync-Warning", `Blotter sync failed - serving journal-derived data (${message})`);
      return res;
    }
    const fromDisk = await readBlotterFromDisk();
    if (fromDisk) {
      const res = NextResponse.json(fromDisk);
      res.headers.set("X-Sync-Warning", `Blotter sync failed - serving cached data (${message})`);
      return res;
    }
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
