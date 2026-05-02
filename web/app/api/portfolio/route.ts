import { NextResponse } from "next/server";
import { readFile, stat } from "fs/promises";
import { join } from "path";
import { readDataFile } from "@tools/data-reader";
import { PortfolioData } from "@tools/schemas/ib-sync";
import { radonFetch } from "@/lib/radonApi";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";

// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const PORTFOLIO_PATH = join(process.cwd(), "..", "data", "portfolio.json");
const CACHE_TTL_MS = 60_000; // 1 minute

const TRADE_LOG_PATH = join(process.cwd(), "..", "data", "trade_log.json");

/** Load ticker → earliest trade date from trade_log.json */
async function loadTradeLogDates(): Promise<Record<string, string>> {
  try {
    const raw = JSON.parse(await readFile(TRADE_LOG_PATH, "utf-8"));
    const trades = Array.isArray(raw) ? raw : (raw?.trades ?? []);
    const dates: Record<string, string> = {};
    for (const t of trades) {
      const ticker = t?.ticker;
      const date = t?.date;
      if (typeof ticker === "string" && typeof date === "string") {
        // Keep the LATEST date per ticker (most recent entry)
        if (!dates[ticker] || date > dates[ticker]) {
          dates[ticker] = date;
        }
      }
    }
    return dates;
  } catch {
    return {};
  }
}

let bgSyncInFlight = false;

/** Returns true when portfolio.json file mtime is older than TTL */
async function isPortfolioStale(): Promise<boolean> {
  try {
    const s = await stat(PORTFOLIO_PATH);
    return Date.now() - s.mtimeMs > CACHE_TTL_MS;
  } catch {
    // File missing or unreadable → treat as stale so we kick off a sync
    return true;
  }
}

/** Fire-and-forget: call FastAPI background sync endpoint */
function triggerBackgroundSync(): void {
  if (bgSyncInFlight) return;
  bgSyncInFlight = true;

  console.log("[Portfolio] Background sync triggered via FastAPI");
  radonFetch("/portfolio/background-sync", { method: "POST", timeout: 5_000 })
    .then(() => {
      console.log("[Portfolio] Background sync accepted");
    })
    .catch((err) => {
      console.warn("[Portfolio] Background sync trigger failed:", err.message);
    })
    .finally(() => {
      bgSyncInFlight = false;
    });
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  // Stale-while-revalidate: kick off background sync if data is >60 s old,
  // but always return the current cached file immediately (non-blocking).
  const stale = await isPortfolioStale();
  if (stale) {
    triggerBackgroundSync();
  }

  try {
    const result = await readDataFile("data/portfolio.json", PortfolioData);
    if (!result.ok) {
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: result.error ?? "Portfolio data not found",
          status: 404,
          code: "NOT_FOUND",
          requestId,
        }),
        requestId,
      );
    }
    // Inject trade_log dates for share PnL entry timestamps
    const tradeLogDates = await loadTradeLogDates();
    const response = NextResponse.json({ ...result.data, trade_log_dates: tradeLogDates });
    return setNoStoreResponseHeaders(response, requestId);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to read portfolio";
    return setNoStoreResponseHeaders(
      jsonApiError({
        message,
        status: 500,
        code: "INTERNAL_ERROR",
        requestId,
      }),
      requestId,
    );
  }
}

export async function POST(): Promise<Response> {
  const requestId = getRequestId();
  try {
    const data = await radonFetch("/portfolio/sync", { method: "POST", timeout: 35_000 });
    const tradeLogDates = await loadTradeLogDates();
    const response = NextResponse.json({ ...data, trade_log_dates: tradeLogDates });
    return setNoStoreResponseHeaders(response, requestId);
  } catch {
    // Sync failed — fall back to cached data file
    const cached = await readDataFile("data/portfolio.json", PortfolioData);
    if (cached.ok) {
      console.warn("[Portfolio] Sync failed, serving cached data");
      const tradeLogDates = await loadTradeLogDates();
      const res = NextResponse.json({ ...cached.data, trade_log_dates: tradeLogDates });
      res.headers.set("X-Sync-Warning", "IB sync failed - serving cached data");
      return setNoStoreResponseHeaders(res, requestId);
    }
    // No cached data either — genuine failure
    return setNoStoreResponseHeaders(
      jsonApiError({
        message: "Sync failed and no cached data available",
        status: 502,
        code: "UPSTREAM_ERROR",
        requestId,
      }),
      requestId,
    );
  }
}
