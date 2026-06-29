import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import { dbExecute } from "@/lib/dbExecute";
import { cachedRead } from "@/lib/dbCache";
import {
  journalRowsToBlotter,
  type BlotterPayload,
  type JournalRow,
} from "@/lib/blotter/fromJournal";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
// Disable Next.js static caching: this handler reads live journal state.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

// The /orders blotter polls this 5000-row journal scan — by far the heaviest
// single read in the app. The journal only changes on a fill, so a longer TTL
// is invisible and coalesces the dashboard polls; staleWhileError keeps the
// blotter rendering through a brief Turso stall. Throws on a real DB error so
// the cache + caller can distinguish that from a genuinely empty journal.
const JOURNAL_CACHE_TTL_MS = 10_000;

async function readJournalRows(): Promise<JournalRow[] | null> {
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
  let rows: JournalRow[] | null;
  try {
    rows = await cachedRead("blotter:journalRows", JOURNAL_CACHE_TTL_MS, readJournalRows, {
      staleWhileError: true,
    });
  } catch {
    // A real DB error (no cached value to fall back on) — preserve the prior
    // behavior of degrading to an empty blotter rather than erroring the route.
    return null;
  }
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
