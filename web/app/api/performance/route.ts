import { NextResponse } from "next/server";
import { readFile, stat, writeFile } from "fs/promises";
import { spawn } from "child_process";
import { join } from "path";
import { isPerformanceBehindPortfolioSync } from "@/lib/performanceFreshness";

export const runtime = "nodejs";

const PERFORMANCE_PATH = join(process.cwd(), "..", "data", "performance.json");
const PORTFOLIO_PATH = join(process.cwd(), "..", "data", "portfolio.json");
const SCRIPTS_DIR = join(process.cwd(), "..", "scripts");
const PYTHON_BIN = process.env.PYTHON_BIN ?? "/usr/bin/python3";
const CACHE_TTL_MS = 15 * 60_000;

async function isPerformanceStale(): Promise<boolean> {
  try {
    const fileStat = await stat(PERFORMANCE_PATH);
    return Date.now() - fileStat.mtimeMs > CACHE_TTL_MS;
  } catch {
    return true;
  }
}

function extractJson(stdout: string): unknown {
  const jsonStart = stdout.indexOf("{");
  if (jsonStart === -1) {
    throw new Error("No JSON output from portfolio_performance.py");
  }
  return JSON.parse(stdout.slice(jsonStart));
}

function runPerformanceSync(): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const proc = spawn(PYTHON_BIN, ["portfolio_performance.py", "--json"], {
      cwd: SCRIPTS_DIR,
      timeout: 180_000,
    });

    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
    proc.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });
    proc.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(stderr || `portfolio_performance.py exited with code ${code}`));
        return;
      }
      try {
        resolve(extractJson(stdout));
      } catch (error) {
        reject(error);
      }
    });
    proc.on("error", reject);
  });
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
  const [stale, cachedPerformance, portfolioSnapshot] = await Promise.all([
    isPerformanceStale(),
    readJsonFile(PERFORMANCE_PATH),
    readJsonFile(PORTFOLIO_PATH),
  ]);

  const shouldSync = !cachedPerformance || stale || isCacheBehindPortfolio(cachedPerformance, portfolioSnapshot);
  if (!shouldSync && cachedPerformance) {
    return NextResponse.json(cachedPerformance);
  }

  try {
    const data = await runPerformanceSync();
    await writeFile(PERFORMANCE_PATH, JSON.stringify(data, null, 2));
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
    const data = await runPerformanceSync();
    await writeFile(PERFORMANCE_PATH, JSON.stringify(data, null, 2));
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to generate performance metrics";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
