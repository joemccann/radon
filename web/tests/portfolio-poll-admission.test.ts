/**
 * @vitest-environment node
 *
 * Market-hours account polling is one read every two seconds
 * (WorkspaceShell). Three tabs share one operator bucket and, on demo,
 * one shell-poll bucket with health, futures quotes, and the flex probe.
 * A budget below that cadence refuses the portfolio read the close ticket
 * sizes from, then the client waits out Retry-After.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { __clearDbCache } from "@/lib/dbCache";
import { demoRateLimit, __resetRateLimitForTests } from "@/lib/demo/rateLimit";
import {
  PORTFOLIO_SNAPSHOT_CACHE_TTL_MS,
  readCachedPortfolioSnapshot,
} from "@/lib/portfolio/portfolioReadCache";
import { rateLimit } from "@/lib/rateLimit";

const ROOT = join(__dirname, "..");
const TABS = 3;
const WINDOW_MS = 60_000;
const RTH_MINUTES = (16 * 60) - (9 * 60 + 30);
const EXTENDED_MINUTES = (9 * 60 + 30 - 4 * 60) + (20 * 60 - 16 * 60);

function source(path: string): string {
  return readFileSync(join(ROOT, path), "utf8");
}

function numericLiteral(raw: string): number {
  const factors = raw.split("*").map(part => Number(part.trim().replaceAll("_", "")));
  return factors.reduce((product, factor) => product * factor, 1);
}

function openPollMs(): number {
  const shell = source("components/WorkspaceShell.tsx");
  const literal = shell.match(/MarketState\.OPEN \? ([\d_]+) :/);
  if (literal) return numericLiteral(literal[1]);
  if (shell.includes("MARKET_HOURS_ACCOUNT_POLL_MS")) {
    return numericLiteral(
      source("lib/accountPollBudget.ts").match(/MARKET_HOURS_ACCOUNT_POLL_MS = ([\d_]+)/)![1],
    );
  }
  throw new Error("market-hours account poll interval missing");
}

function offHoursPollMs(): number {
  const shell = source("components/WorkspaceShell.tsx");
  const literal = shell.match(/MarketState\.OPEN \? [\d_]+ : ([\d_]+)/);
  if (literal) return numericLiteral(literal[1]);
  if (shell.includes("OFF_HOURS_ACCOUNT_POLL_MS")) {
    return numericLiteral(
      source("lib/accountPollBudget.ts").match(/OFF_HOURS_ACCOUNT_POLL_MS = ([\d_]+)/)![1],
    );
  }
  throw new Error("off-hours account poll interval missing");
}

function pollsPerWindow(intervalMs: number, windowMs = WINDOW_MS): number {
  return Math.ceil(windowMs / intervalMs);
}

function declaredPortfolioLimit(): number {
  const route = source("app/api/portfolio/route.ts");
  const literal = route.match(/key: "portfolio:route", limit: (\d+), windowMs: 60_000/);
  if (literal) return Number(literal[1]);
  if (route.includes("limit: PORTFOLIO_GET_LIMIT")) {
    return numericLiteral(
      source("lib/accountPollBudget.ts").match(/PORTFOLIO_GET_LIMIT = ([\d_]+)/)![1],
    );
  }
  throw new Error("portfolio GET admission limit missing");
}

function shellPollsPerMinute(intervalMs: number): number {
  const health = numericLiteral(source("lib/useServiceHealth.ts").match(/const POLL_MS = ([\d_]+)/)![1]);
  const futures = numericLiteral(source("lib/useFuturesQuoteFallback.ts").match(/const REFRESH_MS = ([\d_]+)/)![1]);
  const flex = numericLiteral(
    source("components/FooterTelemetryStrip.tsx").match(/const FLEX_REFRESH_INTERVAL_MS = ([\d_ *]+)/)![1],
  );
  return (
    pollsPerWindow(intervalMs) // portfolio
    + pollsPerWindow(intervalMs) // orders
    + pollsPerWindow(health)
    + 3 * pollsPerWindow(futures)
    + pollsPerWindow(flex)
    + 1 // one risk-free remount inside the same minute
  );
}

describe("market-hours portfolio admission", () => {
  it("admits three open-market tabs and refuses the next portfolio read", () => {
    const limit = declaredPortfolioLimit();
    const required = TABS * pollsPerWindow(openPollMs());
    const key = `route:portfolio:route:rel-324-${required}`;
    const refusedAt: number[] = [];
    for (let i = 0; i < required; i++) {
      if (!rateLimit(key, { limit, windowMs: WINDOW_MS }).ok) refusedAt.push(i);
    }
    expect(refusedAt).toEqual([]);
    expect(rateLimit(key, { limit, windowMs: WINDOW_MS }).ok).toBe(false);
  });

  it("serves a snapshot published on the next open-market poll", async () => {
    process.env.RADON_DB_CACHE_FORCE = "1";
    __clearDbCache();
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const fetcher = vi.fn()
      .mockResolvedValueOnce("snapshot-v1")
      .mockResolvedValueOnce("snapshot-v2");

    await expect(readCachedPortfolioSnapshot(fetcher)).resolves.toMatchObject({ value: "snapshot-v1" });
    vi.setSystemTime(openPollMs());
    await expect(readCachedPortfolioSnapshot(fetcher)).resolves.toMatchObject({ value: "snapshot-v2" });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(PORTFOLIO_SNAPSHOT_CACHE_TTL_MS).toBeLessThanOrEqual(openPollMs());
  });

  it("sizes the shared demo shell budget for three tabs across the equity day", async () => {
    vi.stubEnv("NODE_ENV", "test");
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "");
    __resetRateLimitForTests();

    const openPerTab = shellPollsPerMinute(openPollMs());
    const extendedPerTab = shellPollsPerMinute(offHoursPollMs());
    const minute = TABS * openPerTab;
    const daily = TABS * (RTH_MINUTES * openPerTab + EXTENDED_MINUTES * extendedPerTab);

    expect((await demoRateLimit("I", "rel-324")).limit).toBe(minute);
    expect((await demoRateLimit("J", "rel-324")).limit).toBe(daily);
  });
});

afterEach(() => {
  vi.useRealTimers();
  delete process.env.RADON_DB_CACHE_FORCE;
  __clearDbCache();
});
