import { NextResponse } from "next/server";
import { readFile, stat } from "fs/promises";
import { join } from "path";
import { isPerformanceBehindPortfolioSync, isPortfolioBehindCurrentEtSession } from "@/lib/performanceFreshness";
import { radonFetch } from "@/lib/radonApi";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const PERFORMANCE_PATH = join(process.cwd(), "..", "data", "performance.json");
const PORTFOLIO_PATH = join(process.cwd(), "..", "data", "portfolio.json");
const CACHE_TTL_MS = 15 * 60_000;

async function isPerformanceStale(): Promise<boolean> {
  try {
    const fileStat = await stat(PERFORMANCE_PATH);
    return Date.now() - fileStat.mtimeMs > CACHE_TTL_MS;
  } catch {
    return true;
  }
}

async function readJsonFile(path: string): Promise<Record<string, unknown> | null> {
  try {
    const raw = await readFile(path, "utf-8");
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function extractTimestampValue(data: Record<string, unknown> | null, key: string): string | null {
  const value = data?.[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function isCacheBehindPortfolio(
  performance: Record<string, unknown> | null,
  portfolio: Record<string, unknown> | null,
): boolean {
  const portfolioLastSync = extractTimestampValue(portfolio, "last_sync");
  return isPerformanceBehindPortfolioSync(
    performance
      ? {
          last_sync: extractTimestampValue(performance, "last_sync"),
          as_of: extractTimestampValue(performance, "as_of"),
        }
      : null,
    portfolioLastSync,
  );
}

/**
 * Fire-and-forget background rebuild trigger.
 * 5s timeout, swallow all errors — caller already returned cached data.
 */
function triggerBackgroundRebuild(): void {
  radonFetch("/performance/background", { method: "POST", timeout: 5_000 }).catch(() => {});
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const [stale, cachedPerformance, initialPortfolioSnapshot] = await Promise.all([
    isPerformanceStale(),
    readJsonFile(PERFORMANCE_PATH),
    readJsonFile(PORTFOLIO_PATH),
  ]);

  let portfolioSnapshot = initialPortfolioSnapshot;
  const portfolioLastSync = extractTimestampValue(portfolioSnapshot, "last_sync");

  if (isPortfolioBehindCurrentEtSession(portfolioLastSync)) {
    try {
      const refreshed = await radonFetch<Record<string, unknown>>("/portfolio/sync", {
        method: "POST",
        timeout: 35_000,
      });
      portfolioSnapshot = refreshed;
    } catch {
      // Portfolio sync failed — if we have fresh-enough perf cache, return it
      if (cachedPerformance && !isCacheBehindPortfolio(cachedPerformance, portfolioSnapshot)) {
        return setNoStoreResponseHeaders(NextResponse.json(cachedPerformance), requestId);
      }
      // Otherwise fall through to rebuild evaluation
    }
  }

  const shouldRebuild = !cachedPerformance || stale || isCacheBehindPortfolio(cachedPerformance, portfolioSnapshot);

  if (!shouldRebuild && cachedPerformance) {
    return setNoStoreResponseHeaders(NextResponse.json(cachedPerformance), requestId);
  }

  // SWR: if we have stale cache, return it immediately + trigger background rebuild
  if (cachedPerformance) {
    triggerBackgroundRebuild();
    return setNoStoreResponseHeaders(NextResponse.json(cachedPerformance), requestId);
  }

  // Cold start: no cache at all — must block on full rebuild
  try {
    const data = await radonFetch("/performance", { method: "POST", timeout: 180_000 });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to generate performance metrics";
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message }, { status: 502 }),
      requestId,
    );
  }
}

export async function POST(): Promise<Response> {
  const requestId = getRequestId();
  try {
    const data = await radonFetch("/performance", { method: "POST", timeout: 190_000 });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to generate performance metrics";
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message }, { status: 502 }),
      requestId,
    );
  }
}
