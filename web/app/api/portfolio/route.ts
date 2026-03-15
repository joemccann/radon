import { NextResponse } from "next/server";
import { stat } from "fs/promises";
import { join } from "path";
import { readDataFile } from "@tools/data-reader";
import { PortfolioData } from "@tools/schemas/ib-sync";
import { radonFetch } from "@/lib/radonApi";

export const runtime = "nodejs";

const PORTFOLIO_PATH = join(process.cwd(), "..", "data", "portfolio.json");
const CACHE_TTL_MS = 60_000; // 1 minute

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
  // Stale-while-revalidate: kick off background sync if data is >60 s old,
  // but always return the current cached file immediately (non-blocking).
  const stale = await isPortfolioStale();
  if (stale) {
    triggerBackgroundSync();
  }

  try {
    const result = await readDataFile("data/portfolio.json", PortfolioData);
    if (!result.ok) {
      return NextResponse.json({ error: result.error }, { status: 404 });
    }
    return NextResponse.json(result.data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to read portfolio";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export async function POST(): Promise<Response> {
  try {
    const data = await radonFetch("/portfolio/sync", { method: "POST", timeout: 35_000 });
    return NextResponse.json(data);
  } catch {
    // Sync failed — fall back to cached data file
    const cached = await readDataFile("data/portfolio.json", PortfolioData);
    if (cached.ok) {
      console.warn("[Portfolio] Sync failed, serving cached data");
      const res = NextResponse.json(cached.data);
      res.headers.set("X-Sync-Warning", "IB sync failed - serving cached data");
      return res;
    }
    // No cached data either — genuine failure
    return NextResponse.json(
      { error: "Sync failed and no cached data available" },
      { status: 502 },
    );
  }
}
