import { NextResponse } from "next/server";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { dbExecute } from "@/lib/dbExecute";
import { contentTimestampMs, dbFirstRead, isMissingPayload, staleCollapse, type TimestampedRead } from "@/lib/dbFirstRead";
import { requireRouteAccess } from "@/lib/routeAccess";
import { getFreshnessWindowMs } from "@/lib/serviceHealthWindows";
import { withLiveRow, type VixModelSpreadData, type VixModelSpreadLive } from "@/lib/vixModelSpread";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = "read";

const MISSING_VIX_MODEL_SPREAD = {
  missing: true, status: "missing", scan_time: null, as_of: null,
  data_date: null, count: 0, current: null, series: [],
};

type Payload = Record<string, unknown>;
const ROLLING_FIELDS = ["mean_20", "std_20", "upper_20", "lower_20", "z_20", "mean_252", "std_252", "upper_252", "lower_252", "z_252"] as const;
function isEntry(value: unknown): boolean {
  if (value == null || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Payload;
  return typeof row.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(row.date)
    && ["vix", "vixeq", "cor1m", "model_vix", "spread"].every((key) => typeof row[key] === "number" && Number.isFinite(row[key]))
    && ROLLING_FIELDS.every((key) => row[key] === null || (typeof row[key] === "number" && Number.isFinite(row[key])));
}

function parsePayload(raw: string): Payload {
  const data: unknown = JSON.parse(raw);
  if (data == null || typeof data !== "object" || Array.isArray(data)) throw new Error("Invalid VIX model snapshot");
  const payload = data as Payload;
  delete payload.source_series;
  // Missing heartbeats participate in dbFirstRead's degradation preference.
  if (isMissingPayload(payload)) return payload;
  if ((payload.status !== "ok" && payload.status !== "stale_source")
    || payload.source !== "cboe"
    || !Array.isArray(payload.series) || payload.series.length === 0 || !payload.series.every(isEntry)
    || !isEntry(payload.current) || payload.count !== payload.series.length
    || typeof payload.data_date !== "string") throw new Error("Invalid VIX model snapshot");
  return payload;
}

async function readFromDb(): Promise<TimestampedRead<Payload> | null> {
  const result = await dbExecute({
    // Raw source series support writer conditional requests; the UI needs only
    // the aligned derived history. Omit those private, unused inputs in SQL.
    sql: `SELECT scan_time, json_remove(payload, '$.source_series') AS payload
          FROM scan_snapshots WHERE service = 'vix-model-spread'
          ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  const data = parsePayload(row.payload);
  // Freshness stays on the official snapshot; the live row only extends it.
  const merged = isMissingPayload(data) ? data : withLiveRow(data as unknown as VixModelSpreadData, await readLive());
  return { data: merged as unknown as Payload, timestampMs: contentTimestampMs(data.scan_time) };
}

/** Provisional current-session row from the writer's side snapshot; advisory. */
async function readLive(): Promise<VixModelSpreadLive | null> {
  try {
    const result = await dbExecute({
      sql: `SELECT payload FROM scan_snapshots WHERE service = 'vix-model-spread:live'
            ORDER BY scan_time DESC LIMIT 1`,
      args: [],
    });
    if (result.rows.length === 0) return null;
    const live = JSON.parse(String((result.rows[0] as unknown as { payload: string }).payload)) as Payload;
    if (live.row !== null && !isEntry(live.row)) return null;
    if (typeof live.scan_time !== "string") return null;
    if (live.source !== null && live.source !== "ib" && live.source !== "cboe_delayed") return null;
    return live as unknown as VixModelSpreadLive;
  } catch {
    return null;
  }
}

export async function GET(request: Request): Promise<Response> {
  // The source is approved for the operator's personal, non-commercial use.
  const access = await requireRouteAccess(request, { operatorOnly: true });
  if (!access.ok) return access.response;
  const result = await dbFirstRead({
    fromDb: readFromDb,
    maxAgeMs: getFreshnessWindowMs("vix-model-spread", "closed"),
    label: "vix-model-spread",
    isDegraded: isMissingPayload,
  });
  const response = NextResponse.json(result.ok && result.fresh && !isMissingPayload(result.data)
    ? result.data : staleCollapse(MISSING_VIX_MODEL_SPREAD, result));
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600,
    requestId: getRequestId(), cacheState: "HIT", tags: ["vix-model-spread"],
  });
}
