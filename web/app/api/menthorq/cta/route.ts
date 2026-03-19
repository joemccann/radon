import { NextResponse } from "next/server";
import { spawn } from "child_process";
import { readdir, readFile, stat } from "fs/promises";
import { join } from "path";

export const runtime = "nodejs";

const PROJECT_ROOT = join(process.cwd(), "..");
const CACHE_DIR = join(PROJECT_ROOT, "data", "menthorq_cache");
const STATUS_PATH = join(CACHE_DIR, "health", "cta-sync-latest.json");
const SERVICE_STATUS_PATH = join(PROJECT_ROOT, "data", "service_health", "cta-sync.json");
const LEGACY_STATUS_PATH = join(CACHE_DIR, "cta_sync_status.json");

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

function todayET(now = new Date()): string {
  return now.toLocaleDateString("sv", { timeZone: "America/New_York" });
}

function latestClosedTradingDay(now = new Date()): string {
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const candidate = new Date(et);
  const minutes = candidate.getHours() * 60 + candidate.getMinutes();

  if (!isTradingDay(candidate) || minutes < 16 * 60) {
    candidate.setDate(candidate.getDate() - 1);
  }

  while (!isTradingDay(candidate)) {
    candidate.setDate(candidate.getDate() - 1);
  }

  return todayET(candidate);
}

function isTradingDay(value: Date): boolean {
  const weekday = value.getDay();
  return weekday !== 0 && weekday !== 6;
}

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

function normalizeSyncHealth(raw: Record<string, unknown> | null, targetDate: string): CtaSyncHealth {
  if (!raw) return emptySyncHealth(targetDate);

  const derivedState = typeof raw.state === "string"
    ? raw.state
    : raw.status === "success"
      ? "healthy"
      : raw.status === "error"
        ? "degraded"
        : typeof raw.status === "string"
          ? raw.status
          : "unknown";

  const lastError = raw.last_error && typeof raw.last_error === "object"
    ? raw.last_error as { type?: unknown; message?: unknown }
    : (raw.error_type || raw.error_excerpt)
      ? { type: raw.error_type, message: raw.error_excerpt }
      : null;

  return {
    service: typeof raw.service === "string" ? raw.service : "cta-sync",
    state: derivedState,
    target_date: typeof raw.target_date === "string" ? raw.target_date : targetDate,
    latest_available_date:
      typeof raw.latest_available_date === "string" ? raw.latest_available_date : null,
    last_attempt_started_at:
      typeof raw.last_attempt_started_at === "string"
        ? raw.last_attempt_started_at
        : typeof raw.started_at === "string"
          ? raw.started_at
          : null,
    last_attempt_finished_at:
      typeof raw.last_attempt_finished_at === "string"
        ? raw.last_attempt_finished_at
        : typeof raw.finished_at === "string"
          ? raw.finished_at
          : null,
    last_successful_date:
      typeof raw.last_successful_date === "string" ? raw.last_successful_date : null,
    last_successful_at:
      typeof raw.last_successful_at === "string" ? raw.last_successful_at : null,
    last_cache_path:
      typeof raw.last_cache_path === "string"
        ? raw.last_cache_path
        : typeof raw.cache_path === "string"
          ? raw.cache_path
          : null,
    attempt_count:
      typeof raw.attempt_count === "number" && Number.isFinite(raw.attempt_count)
        ? raw.attempt_count
        : 0,
    last_error: lastError && typeof lastError.type === "string"
      ? {
          type: lastError.type,
          message: typeof lastError.message === "string" ? lastError.message : "",
        }
      : null,
    last_run_source:
      typeof raw.last_run_source === "string"
        ? raw.last_run_source
        : typeof raw.trigger === "string"
          ? raw.trigger
          : null,
    artifacts: raw.artifacts && typeof raw.artifacts === "object"
      ? raw.artifacts as Record<string, string>
      : {},
    message: typeof raw.message === "string" ? raw.message : null,
  };
}

async function readSyncHealth(targetDate: string): Promise<CtaSyncHealth | null> {
  for (const path of [STATUS_PATH, SERVICE_STATUS_PATH, LEGACY_STATUS_PATH]) {
    try {
      const raw = JSON.parse(await readFile(path, "utf-8")) as Record<string, unknown>;
      const looksLikeSyncHealth = typeof raw.service === "string"
        || typeof raw.state === "string"
        || typeof raw.status === "string";
      if (!looksLikeSyncHealth) {
        continue;
      }
      return normalizeSyncHealth(raw, targetDate);
    } catch {
      // try next path
    }
  }
  return null;
}

async function readLatestCta(): Promise<{
  data: { date: string | null; fetched_at: string | null; tables: CtaTables };
  latestFile: string | null;
  mtimeMs: number | null;
}> {
  try {
    const files = await readdir(CACHE_DIR);
    const ctaFiles = files
      .filter((file) => /^cta_\d{4}-\d{2}-\d{2}\.json$/.test(file))
      .sort();

    if (ctaFiles.length === 0) {
      return {
        data: { date: null, fetched_at: null, tables: null },
        latestFile: null,
        mtimeMs: null,
      };
    }

    const latestFile = ctaFiles[ctaFiles.length - 1];
    const raw = JSON.parse(await readFile(join(CACHE_DIR, latestFile), "utf-8")) as Record<string, unknown>;
    const fileStat = await stat(join(CACHE_DIR, latestFile));

    return {
      data: {
        date: typeof raw.date === "string" ? raw.date : null,
        fetched_at: typeof raw.fetched_at === "string" ? raw.fetched_at : null,
        tables: (raw.tables as CtaTables) ?? null,
      },
      latestFile,
      mtimeMs: fileStat.mtimeMs,
    };
  } catch {
    return {
      data: { date: null, fetched_at: null, tables: null },
      latestFile: null,
      mtimeMs: null,
    };
  }
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

  if (typeof child.on === "function") {
    child.on("close", clearInFlight);
    child.on("error", clearInFlight);
  }
  setTimeout(clearInFlight, 30_000);

  if (typeof (child as { unref?: () => void }).unref === "function") {
    (child as { unref: () => void }).unref();
  }
}

export async function GET(): Promise<Response> {
  const expectedDate = latestClosedTradingDay();
  const latest = await readLatestCta();
  const syncHealth = await readSyncHealth(expectedDate);
  const latestAvailableDate = latest.data.date
    ?? latest.latestFile?.replace(/^cta_/, "").replace(/\.json$/, "")
    ?? null;
  const cache_meta = buildCacheMeta(expectedDate, latestAvailableDate, latest.mtimeMs);

  if (
    cache_meta.is_stale === true
    && syncHealth?.state !== "syncing"
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
  const status = latest.latestFile ? 200 : 503;

  return NextResponse.json({
    ...latest.data,
    cache_meta,
    sync_health: syncHealthPayload,
    sync_status: syncStatusPayload,
  }, { status });
}
