import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { dbExecute } from "@/lib/dbExecute";
import {
  journalRowsToBlotter,
  type BlotterPayload,
  type JournalRow,
} from "@/lib/blotter/fromJournal";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
// Disable Next.js static caching: this handler reads live journal state.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

async function readJournalRows(): Promise<JournalRow[] | null> {
  try {
    // 5000-row scan can legitimately run past the default 3s; give it more
    // headroom but still bound it so a stalled pool can't hang the blotter.
    const result = await dbExecute({
      sql: `SELECT payload, filled_at FROM journal ORDER BY filled_at DESC LIMIT 5000`,
      args: [],
    }, { label: "blotter", timeoutMs: 6_000 });
    if (result.rows.length === 0) return null;
    return result.rows.map((r) => {
      const row = r as unknown as { payload: string; filled_at: string | null };
      return {
        payload: JSON.parse(row.payload),
        filled_at: row.filled_at,
      };
    });
  } catch {
    return null;
  }
}

function emptyBlotter(): BlotterPayload {
  return {
    as_of: "",
    summary: { closed_trades: 0, open_trades: 0, total_commissions: 0, realized_pnl: 0 },
    closed_trades: [],
    open_trades: [],
  };
}

async function buildFromJournal(): Promise<BlotterPayload | null> {
  const rows = await readJournalRows();
  if (rows && rows.length > 0) return journalRowsToBlotter(rows);
  return null;
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const blotter = await buildFromJournal();
  if (blotter) return setNoStoreResponseHeaders(NextResponse.json(blotter), requestId);

  return setNoStoreResponseHeaders(NextResponse.json(emptyBlotter()), requestId);
}

export async function POST(): Promise<Response> {
  const requestId = getRequestId();
  try {
    await radonFetch("/journal/rehydrate", { method: "POST", timeout: 300_000 });
    const blotter = await buildFromJournal();
    return setNoStoreResponseHeaders(NextResponse.json(blotter ?? emptyBlotter()), requestId);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Blotter sync failed";
    const blotter = await buildFromJournal();
    if (blotter) {
      const res = NextResponse.json(blotter);
      res.headers.set(
        "X-Sync-Warning",
        `Blotter sync failed - serving Turso journal (${message})`,
      );
      return setNoStoreResponseHeaders(res, requestId);
    }
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message }, { status: 502 }),
      requestId,
    );
  }
}
