/**
 * Provisional current-session rows for daily-close Cboe indicators (VIX model,
 * VIX TS, Panic). The writer keeps today's row in a small `<service>:live`
 * scan snapshot (scripts/utils/index_live.py) and never rewrites the official
 * snapshot for it; routes append it here when it is newer than the official close.
 */
export type ProvisionalSource = "ib" | "cboe_delayed";

export interface ProvisionalMeta {
  source: ProvisionalSource;
  quote_time: string | null;
  scan_time: string;
}

export interface LiveSnapshot<Row> {
  scan_time: string;
  row: Row | null;
  /** Writer-built current card; when absent the row itself is current. */
  current?: unknown;
  source: ProvisionalSource | null;
  quote_time: string | null;
}

interface SeriesPayload<Row> {
  series: Row[];
  /** Official close date; payloads keyed on `as_of` alone (VIX-COR) omit it. */
  data_date?: string | null;
  as_of?: string | null;
  status?: string | null;
  expected_session?: string | null;
}

type Execute = (statement: { sql: string; args: string[] }) => Promise<{ rows: ArrayLike<unknown> }>;

export const LIVE_SNAPSHOT_SQL =
  "SELECT payload FROM scan_snapshots WHERE service = ? ORDER BY scan_time DESC LIMIT 1";

export function liveSnapshotArgs(service: string): string[] {
  return [`${service}:live`];
}

/** Parse a live-snapshot SELECT result, or null when absent, malformed or unreadable. */
export function parseLiveSnapshot<Row extends { date: string }>(
  result: { rows: ArrayLike<unknown> } | null | undefined,
  isRow: (value: unknown) => value is Row,
): LiveSnapshot<Row> | null {
  try {
    if (!result || result.rows.length === 0) return null;
    const live = JSON.parse(String((result.rows[0] as { payload: unknown }).payload)) as Record<string, unknown>;
    if (typeof live.scan_time !== "string") return null;
    if (live.row !== null && !isRow(live.row)) return null;
    if (live.source !== null && live.source !== "ib" && live.source !== "cboe_delayed") return null;
    if (live.quote_time !== null && typeof live.quote_time !== "string") return null;
    return live as unknown as LiveSnapshot<Row>;
  } catch {
    return null;
  }
}

/** Latest live snapshot for `service`, or null when absent, malformed or unreadable. */
export async function readLiveSnapshot<Row extends { date: string }>(
  execute: Execute,
  service: string,
  isRow: (value: unknown) => value is Row,
): Promise<LiveSnapshot<Row> | null> {
  try {
    const result = await execute({
      sql: LIVE_SNAPSHOT_SQL,
      args: liveSnapshotArgs(service),
    });
    return parseLiveSnapshot(result, isRow);
  } catch {
    return null;
  }
}

/**
 * Append the provisional row when it is a session after the official close.
 * A covered expected session lifts a source-lag status, since the row is the
 * session the writer was waiting on.
 */
export function appendProvisionalRow<Row extends { date: string }, P extends SeriesPayload<Row>>(
  payload: P,
  live: LiveSnapshot<Row> | null,
): P & { provisional?: ProvisionalMeta } {
  const row = live?.row;
  const closeDate = "data_date" in payload ? payload.data_date : payload.as_of;
  if (!live || !row || !live.source || !closeDate || row.date <= closeDate) return payload;
  const series = [...payload.series, row];
  const expected = payload.expected_session;
  return {
    ...payload,
    series,
    count: series.length,
    current: live.current ?? row,
    ...("data_date" in payload ? { data_date: row.date } : {}),
    ...("as_of" in payload ? { as_of: row.date } : {}),
    ...(payload.status !== undefined ? { status: expected && row.date >= expected ? "ok" : payload.status } : {}),
    provisional: { source: live.source, quote_time: live.quote_time, scan_time: live.scan_time },
  };
}

const ET_TIME = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

/** Panel copy naming the live source and its quote time; quote_time is already ET wall clock. */
export function provisionalNote(provisional: ProvisionalMeta): string {
  const source = provisional.source === "ib" ? "IB index bars" : "Cboe 15-minute delayed quotes";
  const time = provisional.quote_time ? provisional.quote_time.slice(11, 16) : ET_TIME.format(new Date(provisional.scan_time));
  return `Current session is provisional: ${source} as of ${time} ET, until the official close posts.`;
}
