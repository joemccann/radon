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
  "app/api/flow-analysis/[ticker]/route.ts",
  "app/api/blotter/route.ts",
  "app/api/vcg/route.ts",
  "app/api/internals/route.ts",
  "app/api/portfolio/route.ts",
  "app/api/performance/route.ts",
  "app/api/scanner/route.ts",
  "app/api/regime/route.ts",
  "app/api/gex/route.ts",
  "app/api/gamma-rotation/route.ts",
  "app/api/cash-flows/route.ts",
  "app/api/llm-token-index/route.ts",
  "app/api/service-health/route.ts",
  "app/api/profile/route.ts",
  "app/api/bookmarks/route.ts",
  "app/api/watchlist/route.ts",
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
  "lib/useLlmTokenIndex.ts",
  "lib/useServiceHealth.ts",
  "lib/useTickerFlowReport.ts",
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

// Phase 1 of the Turso source-of-truth migration: routes that already
// dual-write to a Turso table MUST query the DB before falling back to the
// disk JSON cache. This test prevents regressions where someone refactors
// the route and accidentally drops the DB read path.
//
// Each route has its own `readXFromDb` helper that returns the parsed
// payload or null. The pattern is:
//   const fromDb = await readXFromDb();
//   if (fromDb) return ...;
//   // fall through to disk
// Routes that already dual-write to a Turso table (Phase 1 of the
// source-of-truth migration). Each must invoke a DB read function from
// inside its GET handler — the DB-first contract.
const DB_FIRST_ROUTES: { path: string; dbHelperPattern: RegExp }[] = [
  { path: "app/api/vcg/route.ts", dbHelperPattern: /readVcgFromDb\b/ },
  { path: "app/api/gex/route.ts", dbHelperPattern: /readCachedGexFromDb\s*\(/ },
  { path: "app/api/gamma-rotation/route.ts", dbHelperPattern: /readCachedGammaRotationFromDb\s*\(/ },
  { path: "app/api/discover/route.ts", dbHelperPattern: /readDiscoverFromDb\s*\(/ },
  { path: "app/api/menthorq/cta/route.ts", dbHelperPattern: /readLatestCtaFromDb\s*\(/ },
  { path: "app/api/regime/route.ts", dbHelperPattern: /readLatestDbCri\s*\(/ },
  { path: "app/api/scanner/route.ts", dbHelperPattern: /readScannerFromDb\s*\(/ },
  { path: "app/api/flow-analysis/route.ts", dbHelperPattern: /readFlowAnalysisFromDb\s*\(/ },
  { path: "app/api/performance/route.ts", dbHelperPattern: /readPerformanceFromDb\s*\(/ },
  { path: "app/api/portfolio/route.ts", dbHelperPattern: /readPortfolioFromDb\s*\(/ },
  // cash-flows reads via FastAPI proxy which queries Turso server-side
  { path: "app/api/cash-flows/route.ts", dbHelperPattern: /radonFetch\s*\(\s*[`"']\/cash-flows/ },
  { path: "app/api/service-health/route.ts", dbHelperPattern: /\bgetDb\s*\(/ },
];

describe("Turso source-of-truth — routes must invoke a DB read", () => {
  it.each(DB_FIRST_ROUTES)(
    "$path imports + invokes its DB read helper",
    async ({ path, dbHelperPattern }) => {
      const src = await readFile(join(REPO_ROOT, path), "utf8");
      // Strip imports — we want the helper to be CALLED, not just imported.
      const withoutImports = src.replace(/^import .+?(?:;|$)/gms, "");
      expect(withoutImports).toMatch(dbHelperPattern);
    },
  );
});

// `dynamic = "force-dynamic"` only opts the route OUT of Next.js's static
// page cache — it does NOT add a Cache-Control header to the response.
// Without an explicit `Cache-Control: no-store`, browsers and intermediaries
// (Caddy, Cloudflare) heuristically cache the response body and serve a
// stale snapshot until a hard refresh. Commit ee8c401 fixed this for
// /api/flow-analysis after stale ghost positions surfaced; an audit found
// 8 sibling routes with the same bug. This contract scan keeps every
// disk-backed always-fresh route honest.
//
// VCG / Regime / GEX deliberately use setCacheResponseHeaders with a short
// TTL (15s + SWR 120s) — they are NOT enforced here. Performance / cash-flows
// / service-health are exempt for similar reasons (FastAPI proxy or DB-only).
//
// The check is a static-source scan: every response constructor in the
// file (NextResponse.json(...), new Response(...), or jsonApiError(...))
// must either be wrapped in setNoStoreResponseHeaders(...) OR explicitly
// set "cache-control: no-store" on its headers. The portfolio route is
// the canonical example — `setNoStoreResponseHeaders(NextResponse.json(...), requestId)`.
const NO_STORE_ROUTES = [
  "app/api/portfolio/route.ts",
  "app/api/flow-analysis/route.ts",
  "app/api/flow-analysis/[ticker]/route.ts",
  "app/api/journal/route.ts",
  "app/api/discover/route.ts",
  "app/api/blotter/route.ts",
  "app/api/menthorq/cta/route.ts",
  "app/api/internals/route.ts",
  "app/api/orders/route.ts",
  "app/api/scanner/route.ts",
  "app/api/profile/route.ts",
  "app/api/bookmarks/route.ts",
  "app/api/bookmarks/[post_id]/route.ts",
  "app/api/watchlist/route.ts",
  "app/api/watchlist/[symbol]/route.ts",
];

describe("API route handlers — every response must set Cache-Control: no-store", () => {
  it.each(NO_STORE_ROUTES)(
    "%s wraps every response constructor in setNoStoreResponseHeaders",
    async (route) => {
      const src = await readFile(join(REPO_ROOT, route), "utf8");

      // Strip line comments + block comments so we don't match commented-out
      // examples in route files (e.g. JSDoc that shows the legacy pattern).
      const stripped = src
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/(^|[^:])\/\/[^\n]*/g, "$1");

      // Locate every plausible response constructor.
      const constructorPositions = [
        ...stripped.matchAll(/\bNextResponse\.json\s*\(/g),
        ...stripped.matchAll(/\bnew\s+Response\s*\(/g),
        ...stripped.matchAll(/\bjsonApiError\s*\(/g),
        ...stripped.matchAll(/\bjsonError\s*\(/g),
      ];

      // Every route returns at least one response — guard against empty
      // matches silently passing the assertion.
      expect(constructorPositions.length).toBeGreaterThan(0);

      for (const match of constructorPositions) {
        // A response is "covered" if either:
        //   a) it appears inside `setNoStoreResponseHeaders(...)` — look at
        //      the chars BEFORE the match for the wrapping call (the
        //      portfolio route writes `setNoStoreResponseHeaders(NextResponse.json(...), requestId)`),
        //   b) the construction is assigned to a variable that gets passed
        //      to setNoStoreResponseHeaders within the next ~800 chars
        //      (e.g. `const res = NextResponse.json(...); ... return setNoStoreResponseHeaders(res, requestId);`),
        //   c) the response object's headers get set("Cache-Control", "no-store")
        //      explicitly within the next ~800 chars.
        const start = match.index!;
        const before = stripped.slice(Math.max(0, start - 120), start);
        const after = stripped.slice(start, start + 800);

        const wrappedDirectly = /\bsetNoStoreResponseHeaders\s*\(\s*$/.test(
          before.replace(/\s+$/, ""),
        ) || /\bsetNoStoreResponseHeaders\s*\(\s*\n?\s*$/.test(before);

        const passedToHelperLater =
          /\bsetNoStoreResponseHeaders\s*\(/.test(after);

        const explicitNoStoreHeader =
          /headers\.set\s*\(\s*["']Cache-Control["']\s*,\s*["'][^"']*no-store/i.test(after);

        expect(
          wrappedDirectly || passedToHelperLater || explicitNoStoreHeader,
          `Response constructor at offset ${start} in ${route} is not wrapped in setNoStoreResponseHeaders. ` +
            `Snippet: ${stripped.slice(Math.max(0, start - 40), start + 80)}`,
        ).toBe(true);
      }
    },
  );
});
