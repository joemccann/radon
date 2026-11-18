import { NextResponse } from "next/server";
import { dbExecute } from "@/lib/dbExecute";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { isAuthorizedProbeRequest } from "@/lib/probeAuth";
import {
  buildFreshnessPayload,
  type JournalHealthRow,
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

/** journal-sync and journal-gap-sli already heartbeat service_health.
 * One read, same failure slot as the journal leg: a missed row is
 * "not proved live", not a new database_failures name. */
async function readJournalPipeline(): Promise<{
  journalSync: JournalHealthRow | null;
  journalGapSli: JournalHealthRow | null;
}> {
  const result = await dbExecute(
    {
      sql: `SELECT h.service, h.state, h.last_error, h.updated_at,
                   (
                     SELECT e.created_at
                     FROM service_health_events e
                     WHERE e.service = h.service AND e.state != 'ok'
                     ORDER BY e.created_at DESC
                     LIMIT 1
                   ) AS not_ok_since
            FROM service_health h
            WHERE h.service IN ('journal-sync', 'journal-gap-sli')`,
      args: [],
    },
    { label: "freshness journal pipeline" },
  );
  let journalSync: JournalHealthRow | null = null;
  let journalGapSli: JournalHealthRow | null = null;
  for (const row of result.rows) {
    const record = row as unknown as {
      service?: unknown;
      state?: unknown;
      last_error?: unknown;
      updated_at?: unknown;
      not_ok_since?: unknown;
    };
    const health: JournalHealthRow = {
      state: String(record.state ?? ""),
      last_error: record.last_error == null ? null : String(record.last_error),
      updated_at: record.updated_at == null ? null : String(record.updated_at),
      notOkSince: record.not_ok_since == null ? null : String(record.not_ok_since),
    };
    const service = record.service == null ? "" : String(record.service);
    if (service === "journal-sync") journalSync = health;
    if (service === "journal-gap-sli") journalGapSli = health;
  }
  return { journalSync, journalGapSli };
}

async function readJournalLeg(): Promise<
  Pick<ProbeFreshnessInputs, "journalWrittenAt" | "journalSync" | "journalGapSli">
> {
  const [journalWrittenAt, pipeline] = await Promise.all([
    readLatestTimestamp("journal", "SELECT MAX(written_at) AS latest FROM journal"),
    readJournalPipeline(),
  ]);
  return { journalWrittenAt, ...pipeline };
}

async function gatherInputs(): Promise<ProbeFreshnessInputs> {
  const labels = ["relay_tick", "vcg_scan", "gex_scan", "journal"] as const;
  const reads = await Promise.allSettled([
    readRelayRow(),
    readLatestTimestamp("vcg", "SELECT MAX(scan_time) AS latest FROM vcg_snapshots"),
    readLatestTimestamp("gex", "SELECT MAX(scan_time) AS latest FROM gex_snapshots"),
    readJournalLeg(),
  ]);
  const valueAt = <T>(index: number): T | null => {
    const result = reads[index];
    return result.status === "fulfilled" ? (result.value as T | null) : null;
  };
  const journal = valueAt<Awaited<ReturnType<typeof readJournalLeg>>>(3);
  return {
    relayRow: valueAt<RelayHealthRow>(0),
    vcgScanTime: valueAt<string>(1),
    gexScanTime: valueAt<string>(2),
    journalWrittenAt: journal?.journalWrittenAt ?? null,
    journalSync: journal?.journalSync ?? null,
    journalGapSli: journal?.journalGapSli ?? null,
    databaseFailures: labels.filter(
      (_, index) => reads[index].status === "rejected",
    ),
  };
}

export const radonCapability = "internal";

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
