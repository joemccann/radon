import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { dbExecute } from "@/lib/dbExecute";
import { contentTimestampMs, dbFirstRead, isMissingPayload, staleCollapse, type TimestampedRead } from "@/lib/dbFirstRead";
import { requireRouteAccess } from "@/lib/routeAccess";
import { getFreshnessWindowMs } from "@/lib/serviceHealthWindows";
import { highLowLineZone, type HighLowLineData } from "@/lib/highLowLine";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = "read";

const CACHE_PATH = join(process.cwd(), "..", "data", "high_low_line.json");
const MISSING_HIGH_LOW_LINE: HighLowLineData = {
  missing: true, degraded: false, scan_time: null, source_last_modified: null,
  data_date: null, expected_session_date: null, count: 0, weekly_count: 0,
  current: null, weekly_current: null, series: [], weekly_series: [],
  source_hash: null, source: null, baseline: null, settings: null,
};
type RecordValue = Record<string, unknown>;
function record(value: unknown): value is RecordValue {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function finite(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function date(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)
    && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
}
function utcStamp(value: unknown): value is string {
  return typeof value === "string" && /T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$/.test(value)
    && contentTimestampMs(value) !== null;
}
function close(a: number, b: number): boolean { return Math.abs(a - b) <= 1e-8 * Math.max(1, Math.abs(a), Math.abs(b)); }
function bounded(value: unknown, maximum: number): boolean { return value === null || (finite(value) && value >= 0 && value <= maximum); }
function isEntry(value: unknown, weekly: boolean): value is RecordValue & { date: string; period_start: string } {
  if (!record(value) || !date(value.date) || !date(value.period_start) || !date(value.period_end)
    || typeof value.period_complete !== "boolean"
    || !["highs", "lows", "net", "cumulative"].every((key) => finite(value[key]))
    || (value.highs as number) < 0 || (value.lows as number) < 0
    || !close(value.net as number, (value.highs as number) - (value.lows as number))
    || !bounded(value.rsi14, 100) || !bounded(value.stoch_rsi14, 1)
    || !(value.sar === null || finite(value.sar))
    || ![null, "rising", "falling"].includes(value.sar_direction as null | string)
    || typeof value.reset_to_zero !== "boolean" || typeof value.source_fractional !== "boolean"
    || (value.reset_to_zero && value.stoch_rsi14 !== 0)) return false;
  const day = new Date(`${value.date}T00:00:00Z`).getUTCDay();
  if (day < 1 || day > 5) return false;
  if (!weekly) return value.period_start === value.date && value.period_end === value.date && value.period_complete;
  const monday = new Date(`${value.date}T00:00:00Z`);
  monday.setUTCDate(monday.getUTCDate() - day + 1);
  const friday = new Date(monday); friday.setUTCDate(friday.getUTCDate() + 4);
  return value.period_start === monday.toISOString().slice(0, 10) && value.period_end === friday.toISOString().slice(0, 10);
}
function isSeries(value: unknown, weekly: boolean): value is RecordValue[] {
  return Array.isArray(value) && value.length > 0 && value.every((row, index) => isEntry(row, weekly)
    && (index === 0 || row.date > value[index - 1].date)
    && (!weekly || index === 0 || row.period_start > value[index - 1].period_start));
}
function isCurrent(value: unknown, last: RecordValue): boolean {
  return record(value) && Object.keys(last).every((key) => value[key] === last[key])
    && value.zone === highLowLineZone(last.stoch_rsi14 as number | null);
}
function isSource(value: unknown, dataDate: unknown): boolean {
  if (!record(value) || !record(value.symbols)
    || value.symbols.highs !== "!NEWHINYC" || value.symbols.lows !== "!NEWLONYC"
    || !["StockCharts DecisionPoint", "NYSE Common Stock Only New Highs and New Lows"].includes(value.name as string)
    || (value.provider !== undefined && value.provider !== "StockCharts")
    || value.history_start !== "2003-01-02" || value.history_end !== dataDate
    || value.fractional_policy !== "preserved" || !Number.isSafeInteger(value.fractional_observations)
    || (value.fractional_observations as number) < 0
    || !Array.isArray(value.excluded_non_sessions)
    || !value.excluded_non_sessions.every((item) => ["2012-10-29", "2012-10-30"].includes(item))) return false;
  try { const url = new URL(String(value.url)); return url.protocol === "https:" && url.hostname === "stockcharts.com" && !url.username && !url.password; }
  catch { return false; }
}
function snapshot(raw: string): TimestampedRead<HighLowLineData> {
  const data: unknown = JSON.parse(raw);
  if (!record(data) || data.missing !== false || typeof data.degraded !== "boolean"
    || !utcStamp(data.scan_time) || !(data.source_last_modified === null || utcStamp(data.source_last_modified))
    || !date(data.data_date) || !date(data.expected_session_date)
    || data.data_date > data.scan_time.slice(0, 10) || data.expected_session_date > data.scan_time.slice(0, 10)
    || data.data_date > data.expected_session_date
    || (!data.degraded && data.data_date !== data.expected_session_date)
    || !isSeries(data.series, false) || !isSeries(data.weekly_series, true)
    || data.count !== data.series.length || data.weekly_count !== data.weekly_series.length
    || data.data_date !== data.series.at(-1)?.date || data.data_date !== data.weekly_series.at(-1)?.date
    || !isCurrent(data.current, data.series.at(-1)!) || !isCurrent(data.weekly_current, data.weekly_series.at(-1)!)
    || !isSource(data.source, data.data_date)
    || typeof data.source_hash !== "string" || !/^[a-f0-9]{64}$/i.test(data.source_hash)
    || !record(data.baseline) || data.baseline.date !== "2003-01-02" || data.baseline.value !== 0
    || data.baseline.convention !== "before_first_observation" || !record(data.settings)
    || !Object.entries({ rsi_period: 14, stoch_period: 14, smoothing: "Wilder", sar_start: 1,
      sar_increment: 1, sar_max: 1, daily_ohlc: "close_only", weekly_ohlc: "daily_cumulative_range",
      default_interval: "weekly" }).every(([key, value]) => (data.settings as RecordValue)[key] === value)) {
    throw new Error("Invalid common-stock high-low snapshot");
  }
  return { data: data as unknown as HighLowLineData, timestampMs: contentTimestampMs(data.scan_time) };
}
async function readFromDb(): Promise<TimestampedRead<HighLowLineData> | null> {
  const result = await dbExecute({
    sql: `SELECT payload FROM scan_snapshots WHERE service = 'high-low-line' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  }, { label: "high-low-line" });
  return result.rows.length ? snapshot(String(result.rows[0].payload)) : null;
}
async function readFromDisk(): Promise<TimestampedRead<HighLowLineData>> {
  return snapshot(await readFile(CACHE_PATH, "utf-8"));
}
export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, { operatorOnly: true });
  if (!access.ok) return access.response;
  const result = await dbFirstRead({
    fromDb: readFromDb, fromDisk: readFromDisk,
    // Daily writer runs on weekends too; two hours of slack in every market state.
    maxAgeMs: getFreshnessWindowMs("high-low-line", "closed"),
    label: "high-low-line", isDegraded: isMissingPayload,
  });
  const response = NextResponse.json(result.ok && result.fresh ? result.data : staleCollapse(MISSING_HIGH_LOW_LINE, result));
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300, staleWhileRevalidateSeconds: 3600,
    requestId: getRequestId(), cacheState: "HIT", tags: ["high-low-line"],
  });
}
