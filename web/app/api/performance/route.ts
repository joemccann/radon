import { NextResponse } from "next/server";
import { readFile, stat } from "fs/promises";
import { join } from "path";
import { isPerformanceBehindPortfolioSync, isPortfolioBehindCurrentEtSession } from "@/lib/performanceFreshness";
import { radonFetch } from "@/lib/radonApi";

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

export async function GET(): Promise<Response> {
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
      if (cachedPerformance && !isCacheBehindPortfolio(cachedPerformance, portfolioSnapshot)) {
        return NextResponse.json(cachedPerformance);
      }
    }
  }

  const shouldSync = !cachedPerformance || stale || isCacheBehindPortfolio(cachedPerformance, portfolioSnapshot);
  if (!shouldSync && cachedPerformance) {
    return NextResponse.json(cachedPerformance);
  }

  try {
    const data = await radonFetch("/performance", { method: "POST", timeout: 190_000 });
    return NextResponse.json(data);
  } catch (error) {
    if (cachedPerformance) {
      return NextResponse.json(cachedPerformance);
    }
    const message = error instanceof Error ? error.message : "Failed to generate performance metrics";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}

export async function POST(): Promise<Response> {
  try {
    const data = await radonFetch("/performance", { method: "POST", timeout: 190_000 });
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to generate performance metrics";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
