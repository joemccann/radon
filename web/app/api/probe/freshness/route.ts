import { NextResponse } from "next/server";
import type { Client } from "@libsql/client";
import { getDb } from "@/lib/db";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import {
  buildFreshnessPayload,
  type ProbeFreshnessInputs,
  type RelayHealthRow,
} from "@/lib/probeFreshness";

// GET /api/probe/freshness (DUR-16) — the synthetic data-plane freshness
// surface for the Tier-3 off-box prober. AUTH IS THE MIDDLEWARE'S JOB: the
// bearer gate in web/middleware.ts (RADON_PROBE_FRESHNESS_TOKEN, timing-safe)
// is the perimeter, exactly as Clerk is for every other API route.
//
// House rules: always 200 — a quiet market, a missing table, or an
// unreachable DB are data (inapplicable / unproven checks), never 4xx
// console noise. Each Turso read is individually guarded so one missing
// table degrades one check, not the payload. Cache contract: no-store.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function readRelayRow(db: Client): Promise<RelayHealthRow | null> {
  try {
    const result = await db.execute({
      sql: `SELECT state, last_error, updated_at
            FROM service_health
            WHERE service = 'ib-realtime-relay'
            LIMIT 1`,
      args: [],
    });
    const row = result.rows[0];
    if (!row) return null;
    return {
      state: String(row.state),
      last_error: row.last_error == null ? null : String(row.last_error),
      updated_at: row.updated_at == null ? null : String(row.updated_at),
    };
  } catch {
    return null;
  }
}

async function readLatestTimestamp(db: Client, sql: string): Promise<string | null> {
  try {
    const result = await db.execute({ sql, args: [] });
    const value = result.rows[0] ? Object.values(result.rows[0])[0] : null;
    return value == null ? null : String(value);
  } catch {
    return null;
  }
}

async function gatherInputs(): Promise<ProbeFreshnessInputs> {
  let db: Client;
  try {
    db = getDb();
  } catch {
    return { relayRow: null, vcgScanTime: null, gexScanTime: null, journalWrittenAt: null };
  }
  const [relayRow, vcgScanTime, gexScanTime, journalWrittenAt] = await Promise.all([
    readRelayRow(db),
    readLatestTimestamp(db, "SELECT MAX(scan_time) AS latest FROM vcg_snapshots"),
    readLatestTimestamp(db, "SELECT MAX(scan_time) AS latest FROM gex_snapshots"),
    readLatestTimestamp(db, "SELECT MAX(written_at) AS latest FROM journal"),
  ]);
  return { relayRow, vcgScanTime, gexScanTime, journalWrittenAt };
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const inputs = await gatherInputs();
  const payload = buildFreshnessPayload(inputs, new Date());
  return setNoStoreResponseHeaders(NextResponse.json(payload), requestId);
}
