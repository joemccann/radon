import { dbExecute } from "@/lib/dbExecute";
import { withTimeout } from "@/lib/asyncTimeout";
import type { PortfolioData, PortfolioSnapshotSeed } from "@/lib/types";
import { readCachedPortfolioSnapshot } from "@/lib/portfolio/portfolioReadCache";
import {
  isPortfolioSnapshotUnexpectedlyStale,
  PORTFOLIO_SNAPSHOT_STALE_WARNING,
} from "@/lib/portfolioSnapshotFreshness";

const DB_READ_TIMEOUT_MS = 3_000;
const SNAPSHOT_MAX_STALE_MS = 60_000;
const TURSO_STALE_WARNING = "Turso read failed; serving last in-memory portfolio snapshot";
export const PORTFOLIO_SEED_TIMEOUT_MS = 750;

export type PortfolioSnapshot = {
  data: PortfolioData;
  takenAt: string;
  timestampMs: number | null;
};

export type PortfolioSnapshotRead = {
  snapshot: PortfolioSnapshot;
  warning: string | null;
};

export function withoutPortfolioEntryDates<T>(data: T): T {
  if (data == null || typeof data !== "object" || Array.isArray(data)) return data;
  const snapshot = { ...data } as T & Record<string, unknown>;
  delete snapshot.trade_log_dates;
  delete snapshot.contract_open_dates;
  return snapshot;
}

function parseTimestampMs(value: unknown): number | null {
  if (typeof value !== "string" || value.length === 0) return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
}

/** Raised when a stored snapshot cannot be parsed. Distinct from a transport
 *  failure so the route can report corruption rather than DB_UNAVAILABLE. */
export class PortfolioSnapshotCorruptError extends Error {
  readonly code = "SNAPSHOT_CORRUPT" as const;
}

/** Direct-to-cloud read used by the API and the server-rendered portfolio page. */
export async function readPortfolioFromDb(): Promise<PortfolioSnapshot | null> {
  const result = await dbExecute({
    sql: "SELECT taken_at, payload FROM portfolio_snapshots ORDER BY taken_at DESC LIMIT 1",
    args: [],
  }, { label: "portfolio snapshot", timeoutMs: DB_READ_TIMEOUT_MS });
  if (result.rows.length === 0) return null;

  const row = result.rows[0] as unknown as { taken_at?: string; payload?: string };
  if (typeof row.payload !== "string") return null;
  // A truncated or non-JSON payload is PERSISTENCE CORRUPTION, not a database
  // availability problem. Unguarded, the SyntaxError raised inside the cache
  // fetcher could not be helped by staleWhileError (it is not a transport
  // error, and the retry re-reads the same corrupt row and throws
  // identically), and the route filed it as DB_UNAVAILABLE / "Turso portfolio
  // read failed: Unexpected token…". R-257.
  let parsed: PortfolioData;
  try {
    parsed = JSON.parse(row.payload) as PortfolioData;
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    throw new PortfolioSnapshotCorruptError(
      `portfolio_snapshots.payload is not valid JSON (taken_at=${row.taken_at ?? "unknown"}): ${detail}`,
    );
  }
  const data = withoutPortfolioEntryDates(parsed);
  const takenAt = typeof row.taken_at === "string" ? row.taken_at : "";
  return {
    data,
    takenAt,
    timestampMs: parseTimestampMs(data.last_sync) ?? parseTimestampMs(takenAt),
  };
}

function withFreshnessWarning(
  snapshot: PortfolioSnapshot,
  staleWhileError: boolean,
): PortfolioSnapshotRead {
  const warnings: string[] = [];
  if (staleWhileError) warnings.push(TURSO_STALE_WARNING);
  if (isPortfolioSnapshotUnexpectedlyStale(snapshot.timestampMs)) {
    warnings.push(PORTFOLIO_SNAPSHOT_STALE_WARNING);
  }
  return { snapshot, warning: warnings.length > 0 ? warnings.join("; ") : null };
}

/**
 * Shared cached snapshot accessor. It retries a failed direct read once and
 * never initiates IB work, so it is safe in both a route handler and RSC.
 */
export async function readPortfolioSnapshot(): Promise<PortfolioSnapshotRead | null> {
  try {
    const cached = await readCachedPortfolioSnapshot(
      readPortfolioFromDb,
      { staleWhileError: true, maxStaleMs: SNAPSHOT_MAX_STALE_MS },
    );
    return cached.value
      ? withFreshnessWarning(cached.value, cached.staleWhileError)
      : null;
  } catch (error) {
    try {
      const retry = await readPortfolioFromDb();
      if (retry) return withFreshnessWarning(retry, false);
    } catch {
      // Preserve the first read's error and the API's existing status contract.
    }
    throw error;
  }
}

/** Best-effort RSC seed. A miss leaves the client hook's existing GET path intact. */
export async function readPortfolioSnapshotSeed(): Promise<PortfolioSnapshotSeed | undefined> {
  try {
    // Page TTFB must not inherit the API accessor's deliberate second 3s retry.
    // Make one short cache/direct-read attempt; on a cold or wedged Turso pool,
    // render the shell and let the client's bounded GET own recovery instead.
    // Promise.race observes the underlying read if it rejects after this timeout,
    // so a late transport failure cannot become an unhandled rejection.
    const cached = await withTimeout(
      readCachedPortfolioSnapshot(
        readPortfolioFromDb,
        { staleWhileError: true, maxStaleMs: SNAPSHOT_MAX_STALE_MS },
      ),
      PORTFOLIO_SEED_TIMEOUT_MS,
      `portfolio RSC seed timed out after ${PORTFOLIO_SEED_TIMEOUT_MS}ms`,
    );
    if (!cached.value) return undefined;
    const read = withFreshnessWarning(cached.value, cached.staleWhileError);
    return { data: read.snapshot.data, warning: read.warning };
  } catch (err) {
    // The RSC seed path swallowed everything, so an unparseable stored
    // snapshot silently degraded the server-rendered page to the client GET
    // with no log and no trace. Corruption at least says so. R-257.
    if (err instanceof PortfolioSnapshotCorruptError) {
      console.error("[portfolio] stored snapshot is corrupt:", err.message);
    }
    return undefined;
  }
}
