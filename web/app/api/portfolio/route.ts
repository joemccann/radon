import { NextResponse } from "next/server";
import { stat } from "fs/promises";
import { spawn } from "child_process";
import { join } from "path";
import { ibSync } from "@tools/wrappers/ib-sync";
import { readDataFile } from "@tools/data-reader";
import { PortfolioData } from "@tools/schemas/ib-sync";

export const runtime = "nodejs";

const PORTFOLIO_PATH = join(process.cwd(), "..", "data", "portfolio.json");
const SCRIPTS_DIR = join(process.cwd(), "..", "scripts");
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

/** Fire-and-forget: spawn ib_sync.py in the background, non-blocking */
function triggerBackgroundSync(): void {
  if (bgSyncInFlight) return;
  bgSyncInFlight = true;

  console.log("[Portfolio] Background sync triggered");
  const proc = spawn("python3", ["ib_sync.py", "--json"], {
    cwd: SCRIPTS_DIR,
    detached: false,
  });

  proc.stdout.on("data", () => { /* discard */ });
  proc.stderr.on("data", () => { /* discard */ });
  proc.on("close", (code) => {
    if (code !== 0) {
      console.warn(`[Portfolio] Background sync exited with code ${code}`);
    } else {
      console.log("[Portfolio] Background sync complete");
    }
    bgSyncInFlight = false;
  });
  proc.on("error", (err) => {
    console.error("[Portfolio] Background sync error:", err.message);
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
    const result = await ibSync({ sync: true, port: 4001 });

    if (!result.ok) {
      // Sync failed — fall back to cached data file
      const cached = await readDataFile("data/portfolio.json", PortfolioData);
      if (cached.ok) {
        console.warn("[Portfolio] Sync failed, serving cached data:", result.stderr);
        const res = NextResponse.json(cached.data);
        res.headers.set("X-Sync-Warning", "IB sync failed - serving cached data");
        return res;
      }
      // No cached data either — genuine failure
      return NextResponse.json(
        { error: "Sync failed", stderr: result.stderr },
        { status: 502 },
      );
    }

    return NextResponse.json(result.data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Sync failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
