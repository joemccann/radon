import { NextResponse } from "next/server";
import { dbExecute } from "@/lib/dbExecute";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { isAuthorizedProbeRequest } from "@/lib/probeAuth";
import {
  buildFreshnessPayload,
  type ProbeFreshnessInputs,
  type RelayHealthRow,
} from "@/lib/probeFreshness";

// GET /api/probe/freshness (DUR-16) — the synthetic data-plane freshness
// surface for the Tier-3 off-box prober. Middleware and this handler both
// validate the dedicated bearer so rewrites/direct handler invocation cannot
// turn the route into an unauthenticated database reader.
//
// House rules: always 200. Quiet-market applicability remains data, while
// database collection failures are explicit in database_ok/database_failures.
// Each Turso read is bounded independently. Cache contract: no-store.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function readRelayRow(): Promise<RelayHealthRow | null> {
  const result = await dbExecute(
    {
      sql: `SELECT state, last_error, updated_at
            FROM service_health
            WHERE service = 'ib-realtime-relay'
            LIMIT 1`,
      args: [],
    },
    { label: "freshness relay" },
  );
  const row = result.rows[0];
  if (!row) return null;
  return {
    state: String(row.state),
    last_error: row.last_error == null ? null : String(row.last_error),
    updated_at: row.updated_at == null ? null : String(row.updated_at),
  };
}

async function readLatestTimestamp(
  label: string,
  sql: string,
): Promise<string | null> {
  const result = await dbExecute(
    { sql, args: [] },
    { label: `freshness ${label}` },
  );
  const value = result.rows[0] ? Object.values(result.rows[0])[0] : null;
  return value == null ? null : String(value);
}

async function gatherInputs(): Promise<ProbeFreshnessInputs> {
  const labels = ["relay_tick", "vcg_scan", "gex_scan", "journal"] as const;
  const reads = await Promise.allSettled([
    readRelayRow(),
    readLatestTimestamp("vcg", "SELECT MAX(scan_time) AS latest FROM vcg_snapshots"),
    readLatestTimestamp("gex", "SELECT MAX(scan_time) AS latest FROM gex_snapshots"),
    readLatestTimestamp("journal", "SELECT MAX(written_at) AS latest FROM journal"),
  ]);
  const valueAt = <T>(index: number): T | null => {
    const result = reads[index];
    return result.status === "fulfilled" ? (result.value as T | null) : null;
  };
  return {
    relayRow: valueAt<RelayHealthRow>(0),
    vcgScanTime: valueAt<string>(1),
    gexScanTime: valueAt<string>(2),
    journalWrittenAt: valueAt<string>(3),
    databaseFailures: labels.filter(
      (_, index) => reads[index].status === "rejected",
    ),
  };
}

export async function GET(request: Request): Promise<Response> {
  const requestId = getRequestId();
  if (!await isAuthorizedProbeRequest(
    request.headers.get("authorization"),
    process.env.RADON_PROBE_FRESHNESS_TOKEN,
  )) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 401, code: "UNAUTHORIZED", message: "Unauthorized", requestId }),
      requestId,
    );
  }
  const inputs = await gatherInputs();
  const payload = buildFreshnessPayload(inputs, new Date());
  return setNoStoreResponseHeaders(NextResponse.json(payload), requestId);
}
