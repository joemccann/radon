import { NextResponse } from "next/server";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { dbExecute } from "@/lib/dbExecute";
import { snapshotMaxAgeMs } from "@/lib/demo/snapshotWindow";
import { contentTimestampMs, dbFirstRead, isMissingPayload, staleCollapse, type TimestampedRead } from "@/lib/dbFirstRead";
import { getFreshnessWindowMs } from "@/lib/serviceHealthWindows";
import type { AsiaExportLeadData } from "@/lib/asiaExportLead";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = "read";

const MISSING_ASIA_EXPORT_LEAD: AsiaExportLeadData = {
  missing: true,
  scan_time: null,
  source_last_modified: null,
  data_date: null,
  count: 0,
  series: [],
  current: null,
  sources: null,
  comparison: {
    available: false,
    label: "MSCI US forward 12-month EPS",
    reason: "Licensed EPS series not connected",
  },
};

function snapshot(raw: string): TimestampedRead<AsiaExportLeadData> | null {
  const data = JSON.parse(raw) as AsiaExportLeadData | null;
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  // Compare the saved sample's own clock, never its SQL envelope or file mtime.
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

async function readFromDb(): Promise<TimestampedRead<AsiaExportLeadData> | null> {
  const result = await dbExecute({
    sql: `SELECT payload FROM scan_snapshots
          WHERE service = 'asia-export-lead' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  }, { label: "asia-export-lead" });
  if (result.rows.length === 0) return null;
  return snapshot(String(result.rows[0].payload));
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readFromDb,
    // Daily sampling plus two hours of writer slack, shared with health checks.
    maxAgeMs: await snapshotMaxAgeMs(getFreshnessWindowMs("asia-export-lead", "closed")),
    label: "asia-export-lead",
    isDegraded: isMissingPayload,
  });
  const response = NextResponse.json(
    result.ok && result.fresh && !isMissingPayload(result.data)
      ? result.data
      : staleCollapse(MISSING_ASIA_EXPORT_LEAD, result),
  );
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["asia-export-lead"],
  });
}
