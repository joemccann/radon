import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";

import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { BPI_INDEX_SYMBOLS, type BpiIndexEntry, type BpiIndexSymbol } from "@/lib/bpi";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";

// Disable Next.js static caching: this handler reads live disk state
// (data/bpi.json) as the Turso fallback.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "bpi.json");

// The scan timer runs Mon-Fri 21:30 UTC; four days survives long weekends.
const BPI_MAX_AGE_MS = 4 * 24 * 3_600_000;

type BpiDoc = {
  generated_at: string | null;
  indices: Record<string, unknown>;
};

async function readBpiFromDb(): Promise<TimestampedRead<BpiDoc> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT index_symbol, taken_at, payload FROM bpi_snapshots
          WHERE index_symbol IN ('NDX', 'SPX', 'RUT')`,
    args: [],
  });
  if (result.rows.length === 0) return null;

  const indices: Record<string, unknown> = {};
  let newestTakenAt: string | null = null;
  for (const raw of result.rows) {
    const row = raw as unknown as { index_symbol: string; taken_at: string; payload: string };
    indices[row.index_symbol] = JSON.parse(row.payload);
    if (newestTakenAt === null || row.taken_at > newestTakenAt) newestTakenAt = row.taken_at;
  }
  return {
    data: { generated_at: newestTakenAt, indices },
    timestampMs: contentTimestampMs(newestTakenAt),
  };
}

async function readBpiFromDisk(): Promise<TimestampedRead<BpiDoc> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as BpiDoc;
  return { data, timestampMs: contentTimestampMs(data.generated_at) };
}

function missingIndex(symbol: BpiIndexSymbol): BpiIndexEntry {
  return { missing: true, index_symbol: symbol };
}

/**
 * Contract: the response always carries all three indices; an index absent
 * from the served source is its missing:true shape with HTTP 200, never a 4xx.
 */
function withAllIndices(doc: BpiDoc): {
  generated_at: string | null;
  indices: Record<BpiIndexSymbol, unknown>;
} {
  const indices = {} as Record<BpiIndexSymbol, unknown>;
  for (const symbol of BPI_INDEX_SYMBOLS) {
    indices[symbol] = doc.indices?.[symbol] ?? missingIndex(symbol);
  }
  return { generated_at: doc.generated_at ?? null, indices };
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readBpiFromDb,
    fromDisk: readBpiFromDisk,
    maxAgeMs: BPI_MAX_AGE_MS,
    label: "bpi",
  });
  const doc: BpiDoc = result.ok ? result.data : { generated_at: null, indices: {} };
  return setNoStoreResponseHeaders(NextResponse.json(withAllIndices(doc)), requestId);
}
