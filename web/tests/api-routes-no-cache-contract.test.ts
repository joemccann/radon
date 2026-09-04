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
  "app/api/flow-surprise/route.ts",
  "app/api/flow-analysis/route.ts",
  "app/api/flow-analysis/[ticker]/route.ts",
  "app/api/blotter/route.ts",
  "app/api/vcg/route.ts",
  "app/api/internals/route.ts",
  "app/api/portfolio/route.ts",
  "app/api/performance/route.ts",
  "app/api/scanner/route.ts",
  "app/api/scanner/theta/route.ts",
  "app/api/scanner/theta/scan/route.ts",
  "app/api/scanner/strength/route.ts",
  "app/api/scanner/strength/scan/route.ts",
  "app/api/regime/route.ts",
  "app/api/gex/route.ts",
  "app/api/gamma-rotation/route.ts",
  "app/api/cash-flows/route.ts",
  "app/api/llm-token-index/route.ts",
  "app/api/service-health/route.ts",
  "app/api/profile/route.ts",
  "app/api/bookmarks/route.ts",
  "app/api/watchlist/route.ts",
  "app/api/knowledge/search/route.ts",
  "app/api/knowledge/prior-evals/route.ts",
  "app/api/options/rv-ratio/route.ts",
  "app/api/bpi/route.ts",
  "app/api/skew/route.ts",
];

// Client-side fetch sites that hit a disk-backed dynamic route. Each fetch
// MUST request a fresh response with `cache: "no-store"` so the browser/
// Next-data layers never serve a stale snapshot. Defense in depth alongside
// the route-level dynamic export.
//
// `useSyncHook.ts` is the shared GET/POST path for useVcg, useRegime,
// useBlotter, useFlowAnalysis, useGex, usePerformance, useScanner, and
// wrapper hooks such as useDiscover — patching it once covers every
// downstream hook that delegates to it.
const NO_STORE_HOOKS = [
  "lib/useMenthorqCta.ts",
  "lib/useSyncHook.ts",
  "lib/useJournal.ts",
  "lib/usePortfolio.ts",
  "lib/useDiscover.ts",
  "lib/useFlowSurprise.ts",
  "lib/useOrders.ts",
  "lib/useCashFlows.ts",
  "lib/useLlmTokenIndex.ts",
  "lib/useServiceHealth.ts",
  "lib/useTickerFlowReport.ts",
  "lib/useRvRatio.ts",
  "lib/useBpi.ts",
  "lib/useHeadlines.ts",
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
    if (fetchPositions.length === 0) {
      expect(src).toMatch(/\buseSyncHook\b/);
      return;
    }

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
  { path: "app/api/orders/route.ts", dbHelperPattern: /readOrdersSnapshotFromDb\s*\(/ },
  { path: "app/api/vcg/route.ts", dbHelperPattern: /readVcgFromDb\b/ },
  { path: "app/api/gex/route.ts", dbHelperPattern: /readCachedGexFromDb\s*\(/ },
  { path: "app/api/gamma-rotation/route.ts", dbHelperPattern: /readCachedGammaRotationFromDb\s*\(/ },
  { path: "app/api/discover/route.ts", dbHelperPattern: /readDiscoverFromDb\s*\(/ },
  { path: "app/api/menthorq/cta/route.ts", dbHelperPattern: /readLatestCtaFromDb\s*\(/ },
  { path: "app/api/regime/route.ts", dbHelperPattern: /readLatestDbCri\s*\(/ },
  { path: "app/api/scanner/route.ts", dbHelperPattern: /readScannerFromDb\s*\(/ },
  { path: "app/api/flow-analysis/route.ts", dbHelperPattern: /readFlowAnalysisFromDb\s*\(/ },
  { path: "app/api/performance/route.ts", dbHelperPattern: /readPerformanceFromDb\s*\(/ },
  { path: "app/api/portfolio/route.ts", dbHelperPattern: /readPortfolioSnapshot\s*\(/ },
  { path: "app/api/journal/route.ts", dbHelperPattern: /readJournalFromDb\s*\(/ },
  // cash-flows reads via FastAPI proxy which queries Turso server-side
  { path: "app/api/cash-flows/route.ts", dbHelperPattern: /radonFetch\s*\(\s*[`"']\/cash-flows/ },
  { path: "app/api/service-health/route.ts", dbHelperPattern: /\bdbExecute\s*\(/ },
  // Formerly disk-only routes (2026-07-02 SOT sweep): producers mirror to
  // Turso (catalysts table / generic scan_snapshots / cri_snapshots +
  // menthorq_cta), routes read DB-first with the data/ JSON as fallback.
  { path: "app/api/catalysts/route.ts", dbHelperPattern: /readCatalystsFromDb\s*\(/ },
  { path: "app/api/leap/route.ts", dbHelperPattern: /readLeapFromDb\s*\(/ },
  { path: "app/api/garch-convergence/route.ts", dbHelperPattern: /readGarchFromDb\s*\(/ },
  { path: "app/api/flow-surprise/route.ts", dbHelperPattern: /readFlowSurpriseFromDb\s*\(/ },
  { path: "app/api/internals/route.ts", dbHelperPattern: /readLatestDbCri\s*\(/ },
  { path: "app/api/options/rv-ratio/route.ts", dbHelperPattern: /readRvRatioFromDb\s*\(/ },
  { path: "app/api/bpi/route.ts", dbHelperPattern: /readBpiFromDb\b/ },
];

// Deliberately NOT DB-first (re-confirmed 2026-07-02) — do not "fix" these:
// - informed-flow/[ticker] + futures/chain: proxy-first to FastAPI, which
//   runs on the same host as the producer/cache; the proxy already bridges
//   hosts and the disk read is a last-resort fallback.
// - ticker/seasonality + ticker/info: web-tier TTL caches of EXTERNAL API
//   data (UW/Exa/Anthropic) — losable, regenerated on demand, written and
//   read by the same Next.js process. Turso is the source of truth for
//   Radon-generated data, not third-party cache material.

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

const ORDER_DB_ONLY_ROUTES: { path: string; dbHelperPattern: RegExp }[] = [
  { path: "app/api/orders/route.ts", dbHelperPattern: /readOrdersSnapshotFromDb\s*\(/ },
  { path: "app/api/orders/cancel/route.ts", dbHelperPattern: /readOrdersSnapshotFromDb\s*\(/ },
  { path: "app/api/orders/modify/route.ts", dbHelperPattern: /readOrdersSnapshotFromDb\s*\(/ },
  { path: "app/api/orders/place/route.ts", dbHelperPattern: /readOrdersSnapshotFromDb\s*\(/ },
];

describe("Turso source-of-truth — orders routes must not read data/orders.json", () => {
  it.each(ORDER_DB_ONLY_ROUTES)(
    "$path reads orders from Turso and never from the flat orders file",
    async ({ path, dbHelperPattern }) => {
      const src = await readFile(join(REPO_ROOT, path), "utf8");
      const stripped = src
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/(^|[^:])\/\/[^\n]*/g, "$1");

      expect(stripped).toMatch(dbHelperPattern);
      expect(stripped).not.toContain("data/orders.json");
      expect(stripped).not.toMatch(/\breadDataFile\b/);
      expect(stripped).not.toMatch(/\bOrdersData\b/);
    },
  );
});

describe("Turso source-of-truth — portfolio route must not read flat JSON", () => {
  it("app/api/portfolio/route.ts reads the snapshot and optional trade dates from Turso only", async () => {
    const src = await readFile(join(REPO_ROOT, "app/api/portfolio/route.ts"), "utf8");
    const stripped = src
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(^|[^:])\/\/[^\n]*/g, "$1");

    expect(stripped).toMatch(/readPortfolioSnapshot\s*\(/);
    expect(stripped).toMatch(/FROM\s+journal/i);
    expect(stripped).not.toContain("data/portfolio.json");
    expect(stripped).not.toContain("data/trade_log.json");
    expect(stripped).not.toMatch(/\breadDataFile\b/);
    expect(stripped).not.toMatch(/\bPortfolioData\b/);
  });
});

const JOURNAL_DB_ONLY_ROUTES: { path: string; dbHelperPattern: RegExp }[] = [
  { path: "app/api/journal/route.ts", dbHelperPattern: /readJournalFromDb\s*\(/ },
  { path: "app/api/journal/sync/route.ts", dbHelperPattern: /importReconciliationSnapshotToJournal\s*\(/ },
];

describe("Turso source-of-truth — journal routes must not read flat JSON", () => {
  it.each(JOURNAL_DB_ONLY_ROUTES)(
    "$path reads/writes journal state through Turso helpers",
    async ({ path, dbHelperPattern }) => {
      const src = await readFile(join(REPO_ROOT, path), "utf8");
      const stripped = src
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/(^|[^:])\/\/[^\n]*/g, "$1");

      expect(stripped).toMatch(dbHelperPattern);
      expect(stripped).not.toContain("trade_log.json");
      expect(stripped).not.toContain("reconciliation.json");
      expect(stripped).not.toMatch(/\breadFile\b/);
      expect(stripped).not.toMatch(/\bwriteFile\b/);
      expect(stripped).not.toMatch(/\brunJournalSync\b/);
    },
  );
});

describe("Turso source-of-truth — PI route must not read portfolio/journal flat JSON", () => {
  it("app/api/pi/route.ts serves portfolio and journal commands from Turso", async () => {
    const src = await readFile(join(REPO_ROOT, "app/api/pi/route.ts"), "utf8");
    const stripped = src
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(^|[^:])\/\/[^\n]*/g, "$1");

    expect(stripped).toMatch(/FROM\s+portfolio_snapshots/i);
    expect(stripped).toMatch(/readJournalFromDb\s*\(/);
    expect(stripped).not.toContain("portfolio.json");
    expect(stripped).not.toContain("trade_log.json");
    expect(stripped).not.toMatch(/\breadLocalJsonFile\b/);
    expect(stripped).not.toMatch(/\breadFile\b/);
  });
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
  "app/api/flow-surprise/route.ts",
  "app/api/blotter/route.ts",
  "app/api/menthorq/cta/route.ts",
  "app/api/internals/route.ts",
  "app/api/orders/route.ts",
  "app/api/scanner/route.ts",
  "app/api/scanner/theta/route.ts",
  "app/api/scanner/theta/scan/route.ts",
  "app/api/scanner/strength/route.ts",
  "app/api/scanner/strength/scan/route.ts",
  "app/api/profile/route.ts",
  "app/api/bookmarks/route.ts",
  "app/api/bookmarks/[post_id]/route.ts",
  "app/api/watchlist/route.ts",
  "app/api/watchlist/[symbol]/route.ts",
  "app/api/knowledge/search/route.ts",
  "app/api/knowledge/prior-evals/route.ts",
  "app/api/bpi/route.ts",
  "app/api/skew/route.ts",
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
