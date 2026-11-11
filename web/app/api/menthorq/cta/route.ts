import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { spawn } from "child_process";
import { join } from "path";
import { dbExecute } from "@/lib/dbExecute";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { lastCompletedSessionDate } from "@/lib/marketCalendar";
import { reconcileCtaTables } from "@/lib/ctaPercentiles";

export const runtime = "nodejs";
// Disable Next.js's default static caching for route handlers. Without this the
// first response is frozen and subsequent requests serve stale CTA state until
// the dev server restarts. See commit history: prior incidents fixed Apr 30
// (post-startup hook), but the underlying response-caching window remained.
export const dynamic = "force-dynamic";

const PROJECT_ROOT = join(/* turbopackIgnore: true */ process.cwd(), "..");
// A successful sync lands one row per closed trading day, and the expected-date
// comparison in buildCacheMeta is what gates staleness; this only labels `fresh`.
const CTA_MAX_AGE_MS = 96 * 60 * 60_000;

type CtaTables = {
  main: Array<Record<string, unknown>>;
  index: Array<Record<string, unknown>>;
  commodity: Array<Record<string, unknown>>;
  currency: Array<Record<string, unknown>>;
} | null;

type CtaSyncHealth = {
  service: string;
  state: string;
  target_date: string | null;
  latest_available_date: string | null;
  last_attempt_started_at: string | null;
  last_attempt_finished_at: string | null;
  last_successful_date: string | null;
  last_successful_at: string | null;
  last_cache_path: string | null;
  attempt_count: number;
  last_error: { type: string; message: string } | null;
  last_run_source: string | null;
  artifacts: Record<string, string>;
  message: string | null;
};

let bgSyncInFlight = false;

function emptySyncHealth(targetDate: string): CtaSyncHealth {
  return {
    service: "cta-sync",
    state: "unknown",
    target_date: targetDate,
    latest_available_date: null,
    last_attempt_started_at: null,
    last_attempt_finished_at: null,
    last_successful_date: null,
    last_successful_at: null,
    last_cache_path: null,
    attempt_count: 0,
    last_error: null,
    last_run_source: null,
    artifacts: {},
    message: null,
  };
}

// service_health.state vocabulary -> the sync-health vocabulary CtaPage reads.
const SERVICE_STATE_TO_SYNC_STATE: Record<string, string> = {
  ok: "healthy",
  error: "degraded",
};

function parseLastError(raw: unknown): CtaSyncHealth["last_error"] {
  if (typeof raw !== "string" || raw.length === 0) return null;
  try {
    const parsed = JSON.parse(raw) as { type?: unknown; message?: unknown };
    return {
      type: typeof parsed.type === "string" ? parsed.type : "error",
      message: typeof parsed.message === "string" ? parsed.message : raw,
    };
  } catch {
    return { type: "error", message: raw };
  }
}

/** The cta-sync row in service_health; the detail files stay with the writer. */
async function readSyncHealthFromDb(targetDate: string): Promise<TimestampedRead<CtaSyncHealth> | null> {
  const result = await dbExecute({
    sql: `SELECT state, last_attempt_started_at, last_attempt_finished_at, last_error, updated_at
          FROM service_health WHERE service = 'cta-sync'`,
    args: [],
  }, { label: "cta-sync-health" });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as {
    state: string;
    last_attempt_started_at: string | null;
    last_attempt_finished_at: string | null;
    last_error: string | null;
    updated_at: string | null;
  };
  return {
    data: {
      ...emptySyncHealth(targetDate),
      state: SERVICE_STATE_TO_SYNC_STATE[row.state] ?? row.state,
      last_attempt_started_at: row.last_attempt_started_at,
      last_attempt_finished_at: row.last_attempt_finished_at,
      last_error: parseLastError(row.last_error),
    },
    timestampMs: contentTimestampMs(row.updated_at),
  };
}

async function readSyncHealth(targetDate: string): Promise<CtaSyncHealth | null> {
  const result = await dbFirstRead({
    fromDb: () => readSyncHealthFromDb(targetDate),
    maxAgeMs: CTA_MAX_AGE_MS,
    label: "cta-sync-health",
  });
  return result.ok ? result.data : null;
}

type LatestCta = {
  date: string | null;
  fetched_at: string | null;
  tables: CtaTables;
};

async function readLatestCtaFromDb(): Promise<TimestampedRead<LatestCta> | null> {
  const result = await dbExecute({
    sql: `SELECT date, payload, fetched_at FROM menthorq_cta ORDER BY date DESC LIMIT 1`,
    args: [],
  }, { label: "menthorq-cta" });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { date: string; payload: string; fetched_at: string };
  const raw = JSON.parse(row.payload) as Record<string, unknown>;
  return {
    data: {
      date: typeof raw.date === "string" ? raw.date : row.date,
      fetched_at:
        typeof raw.fetched_at === "string" ? raw.fetched_at : row.fetched_at,
      tables: reconcileCtaTables((raw.tables as CtaTables) ?? null),
    },
    timestampMs: contentTimestampMs(row.fetched_at),
  };
}

async function readLatestCta(): Promise<TimestampedRead<LatestCta> | null> {
  const result = await dbFirstRead({
    fromDb: readLatestCtaFromDb,
    maxAgeMs: CTA_MAX_AGE_MS,
    label: "menthorq-cta",
    isDegraded: (data) => !data.tables,
  });
  return result.ok && result.data.tables
    ? { data: result.data, timestampMs: result.timestampMs }
    : null;
}

function buildCacheMeta(
  expectedDate: string,
  latestAvailableDate: string | null,
  mtimeMs: number | null,
): Record<string, string | number | boolean | null> {
  const ageSeconds = mtimeMs == null ? null : Math.round((Date.now() - mtimeMs) / 1000);
  const isStale = latestAvailableDate == null || latestAvailableDate !== expectedDate;
  const staleReason = latestAvailableDate == null
    ? "missing_cache"
    : latestAvailableDate !== expectedDate
      ? "behind_target"
      : "fresh";

  return {
    last_refresh: mtimeMs == null ? null : new Date(mtimeMs).toISOString(),
    age_seconds: ageSeconds,
    is_stale: isStale,
    expected_date: expectedDate,
    target_date: expectedDate,
    latest_available_date: latestAvailableDate,
    latest_cache_date: latestAvailableDate,
    stale_reason: staleReason,
  };
}

function triggerBackgroundSync(expectedDate: string): void {
  if (bgSyncInFlight) return;
  bgSyncInFlight = true;

  const child = spawn(
    "bash",
    ["scripts/run_cta_sync.sh", "--target-date", expectedDate],
    {
      cwd: PROJECT_ROOT,
      env: { ...process.env, RADON_CTA_SYNC_SOURCE: "api" },
      detached: true,
      stdio: "ignore",
    },
  );

  const clearInFlight = () => {
    bgSyncInFlight = false;
  };

  // The flag clears ONLY when the child is gone (close / spawn error). A
  // wall-clock reset let the next stale poll spawn a second sync against the
  // same lock and status file while the first was still running. Node emits
  // "close" for every exit, including a signal kill, so a dead child always
  // reopens the gate; a hung one keeps it shut, which is the point.
  if (typeof child.on === "function") {
    child.on("close", (code: number | null) => {
      if (code !== 0) {
        console.error(
          `[cta-sync] background sync exited ${code} (target ${expectedDate})`,
        );
      }
      clearInFlight();
    });
    child.on("error", (err: Error) => {
      console.error(
        `[cta-sync] background sync spawn failed (target ${expectedDate}): ${err.message}`,
      );
      clearInFlight();
    });
  }

  if (typeof (child as { unref?: () => void }).unref === "function") {
    (child as { unref: () => void }).unref();
  }
}

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "menthorq/cta:route", limit: 20, windowMs: 60_000 }, durableRateTier: "A" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const expectedDate = lastCompletedSessionDate();
  const latest = await readLatestCta();
  const syncHealth = await readSyncHealth(expectedDate);
  const latestAvailableDate = latest?.data.date ?? null;
  const cache_meta = buildCacheMeta(expectedDate, latestAvailableDate, latest?.timestampMs ?? null);

  // Allow re-trigger if the "syncing" state is stale (> 5 min since last attempt).
  // A dead-process lock can leave the service permanently in "syncing" otherwise.
  const syncingIsStale =
    syncHealth?.state === "syncing" &&
    syncHealth?.last_attempt_started_at != null &&
    Date.now() - new Date(syncHealth.last_attempt_started_at).getTime() > 5 * 60 * 1000;

  if (
    cache_meta.is_stale === true
    && (syncHealth?.state !== "syncing" || syncingIsStale)
    && expectedDate !== latestAvailableDate
  ) {
    triggerBackgroundSync(expectedDate);
  }

  const syncHealthPayload = syncHealth
    ? {
        ...syncHealth,
        target_date: expectedDate,
        latest_available_date: syncHealth.latest_available_date ?? latestAvailableDate,
      }
    : null;
  const syncStatusPayload = syncHealth
    ? {
        service: syncHealth.service,
        status:
          syncHealth.state === "healthy"
            ? "success"
            : syncHealth.state === "degraded"
              ? "error"
              : syncHealth.state,
        trigger: syncHealth.last_run_source,
        target_date: expectedDate,
        started_at: syncHealth.last_attempt_started_at,
        finished_at: syncHealth.last_attempt_finished_at,
        duration_ms: null,
        attempt_count: syncHealth.attempt_count,
        cache_path: syncHealth.last_cache_path,
        error_type: syncHealth.last_error?.type ?? null,
        error_excerpt: syncHealth.last_error?.message ?? syncHealth.message ?? null,
        artifact_log_path: syncHealth.artifacts.context ?? null,
      }
    : null;
  const status = latest ? 200 : 503;

  return setNoStoreResponseHeaders(
    NextResponse.json({
      ...(latest?.data ?? { date: null, fetched_at: null, tables: null }),
      cache_meta,
      sync_health: syncHealthPayload,
      sync_status: syncStatusPayload,
    }, { status }),
    requestId,
  );
}
