import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const REPO_ROOT = join(__dirname, "..");

// Disk-backed GET routes that read live JSON state from the data/ tree.
// All of these MUST opt out of Next.js's static-route cache, otherwise the
// first GET response gets frozen for the lifetime of the dev server and
// subsequent calls serve stale data even after the underlying file changes.
// The CTA route was the canary; commit 0575bc1 fixed it. This contract test
// keeps the rest of the family in line.
const DYNAMIC_ROUTES = [
  "app/api/menthorq/cta/route.ts",
  "app/api/journal/route.ts",
  "app/api/discover/route.ts",
  "app/api/flow-analysis/route.ts",
  "app/api/blotter/route.ts",
  "app/api/vcg/route.ts",
  "app/api/internals/route.ts",
  "app/api/portfolio/route.ts",
  "app/api/performance/route.ts",
  "app/api/scanner/route.ts",
  "app/api/regime/route.ts",
  "app/api/gex/route.ts",
  "app/api/cash-flows/route.ts",
];

// Client-side fetch sites that hit a disk-backed dynamic route. Each fetch
// MUST request a fresh response with `cache: "no-store"` so the browser/
// Next-data layers never serve a stale snapshot. Defense in depth alongside
// the route-level dynamic export.
//
// `useSyncHook.ts` is the shared GET path for useVcg, useRegime, useBlotter,
// useFlowAnalysis, useGex, usePerformance, useScanner — patching it once
// covers all seven downstream hooks.
const NO_STORE_HOOKS = [
  "lib/useMenthorqCta.ts",
  "lib/useSyncHook.ts",
  "lib/useJournal.ts",
  "lib/usePortfolio.ts",
  "lib/useDiscover.ts",
  "lib/useOrders.ts",
  "lib/useCashFlows.ts",
];

describe("API route handlers — must export dynamic = 'force-dynamic'", () => {
  it.each(DYNAMIC_ROUTES)("%s opts out of static caching", async (route) => {
    const src = await readFile(join(REPO_ROOT, route), "utf8");
    expect(src).toMatch(/export\s+const\s+dynamic\s*=\s*["']force-dynamic["']/);
  });
});

describe("client hooks/components — every fetch must use cache: 'no-store'", () => {
  it.each(NO_STORE_HOOKS)("%s requests fresh responses", async (hook) => {
    const src = await readFile(join(REPO_ROOT, hook), "utf8");

    // Find every fetch(...) call. useSyncHook fetches a parameterised URL
    // (`fetch(endpoint, ...)`), the others fetch literal /api/ strings —
    // either way the same cache-policy rule applies.
    const fetchPositions = [...src.matchAll(/\bfetch\s*\(/g)];
    expect(fetchPositions.length).toBeGreaterThan(0);

    for (const match of fetchPositions) {
      const start = match.index!;
      // Scan up to the matching closing paren or 600 chars, whichever is sooner.
      const window = src.slice(start, start + 600);
      expect(window).toMatch(/cache\s*:\s*["']no-store["']/);
    }
  });
});
