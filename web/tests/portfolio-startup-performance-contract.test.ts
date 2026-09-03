/**
 * @vitest-environment jsdom
 *
 * Behavioural contracts for the portfolio startup path (T-167, T-170):
 *  - shell navigation never arms Next.js viewport prefetch,
 *  - the all-routes workspace chunk never pulls the portfolio surface,
 *  - /portfolio is seeded from the server, not a client GET waterfall,
 *  - the FRED read is served from a bounded server cache.
 *
 * These were source-string greps. Each now drives the real module.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// T-238: two cases here `await import(...)` the workspace module graph, so the
// FIRST of them pays Vite's transform of that whole graph inside its own
// per-test budget. Measured on this runner: a hard `Test timed out in 5000ms`
// twice in a row at load average 42, then 6852 / 7854 / 8068 ms at load ~35
// once the ceiling was raised. A host running two weekend loops at once is the
// normal condition here, not the exception. 20s is the same ceiling T-161 set
// for the two jsdom suites it fixed and keeps the worst measured case under
// half the budget. This raises the ceiling only; it does NOT re-enable the
// blanket `retry` T-161 deliberately removed, so a genuine hang still fails.
vi.setConfig({ testTimeout: 20_000, hookTimeout: 20_000 });

const WEB = join(import.meta.dirname, "..");
const source = (path: string) => readFileSync(join(WEB, path), "utf8");

// --- next/link: surface the prefetch prop the shell must pass -------------
vi.mock("next/link", () => ({
  default: ({ children, href, prefetch, ...rest }: Record<string, unknown> & { children?: React.ReactNode }) =>
    React.createElement(
      "a",
      { ...rest, href: String(href), "data-prefetch": String(prefetch) },
      children as React.ReactNode,
    ),
}));

// --- next/dynamic: record each lazy boundary the shell declares -----------
type ChunkLoader = () => Promise<{ default: React.ComponentType<unknown> }>;
const chunkLoaders: ChunkLoader[] = [];
vi.mock("next/dynamic", () => ({
  default: (loader: ChunkLoader) => {
    const index = chunkLoaders.push(loader) - 1;
    const LazyChunk = () => React.createElement("div", { "data-testid": `lazy-chunk-${index}` });
    LazyChunk.displayName = `LazyChunk(${index})`;
    return LazyChunk;
  },
}));

// --- shell/nav dependencies ----------------------------------------------
const usePortfolioSpy = vi.fn(() => ({
  data: null, loading: false, syncing: false, error: null, lastSync: null, syncNow: () => {},
}));
vi.mock("@/lib/usePortfolio", () => ({ usePortfolio: usePortfolioSpy }));

const readSeedSpy = vi.fn();
vi.mock("@/lib/portfolio/readPortfolioSnapshot.server", () => ({
  readPortfolioSnapshotSeed: readSeedSpy,
}));

const stub = (testId: string) => ({
  default: () => React.createElement("div", { "data-testid": testId }),
});
vi.mock("@/components/Header", () => stub("header"));
vi.mock("@/components/MetricCards", () => stub("metric-cards"));
vi.mock("@/components/dashboard/DashboardSurface", () => stub("dashboard-surface"));
vi.mock("@/components/ChatLauncher", () => stub("chat-launcher"));
vi.mock("@/components/DemoWelcomeModal", () => stub("demo-welcome"));
vi.mock("@/components/mobile/MobileShell", () => stub("mobile-shell"));
vi.mock("@/components/CommandPalette", () => stub("command-palette"));
vi.mock("@/components/FooterTelemetryStrip", () => stub("footer-telemetry"));
vi.mock("@/components/FuturesStrip", () => stub("futures-strip"));
vi.mock("@/components/OfflineBanner", () => stub("offline-banner"));
vi.mock("@/components/Toast", () => stub("toasts"));

vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActions: () => ({ drainNotifications: () => [], setOrdersUpdater: () => {} }),
}));
// Every unlisted key is a no-op setter; the shell only reads these four.
const tickerDetailStub = new Proxy(
  { chainContracts: [], depthSymbol: null, depthSymbols: [], depthFutureExpiry: null } as Record<string, unknown>,
  { get: (target, key: string) => (key in target ? target[key] : () => {}) },
);
vi.mock("@/lib/TickerDetailContext", () => ({ useTickerDetail: () => tickerDetailStub }));
vi.mock("@/lib/offline/OfflineStatusContext", () => ({ useOfflineStatus: () => ({ offline: false }) }));
vi.mock("@/lib/ThemeContext", () => ({ useTheme: () => ({ theme: "dark", toggleTheme: () => {} }) }));
vi.mock("@/lib/RealtimeAuthContext", () => ({ useRealtimeAuth: () => async () => null }));
// The shell reads the root realtime context (useRealtimePrices), not
// @/lib/usePrices directly. Record every `connected` value the shell observes
// so the perf cases below provably measure the CONNECTED branch — a dead mock
// here once left them timing the disconnected fallback (T-388).
const observedConnected: boolean[] = [];
const realtimeValue = {
  prices: {},
  fundamentals: {},
  depths: {},
  tape: {},
  connected: true,
  ibConnected: true,
  ibIssue: null,
  ibStatusMessage: null,
  error: null,
  reconnect: () => {},
  getSnapshot: async () => ({}),
  publishSubscriptions: () => {},
};
vi.mock("@/lib/RealtimePricesContext", () => ({
  useRealtimePrices: () => {
    observedConnected.push(realtimeValue.connected);
    return realtimeValue;
  },
}));
vi.mock("@/lib/useOrders", () => ({
  useOrders: () => ({ data: null, loading: false, syncing: false, error: null, lastSync: null, syncNow: () => {} }),
}));
vi.mock("@/lib/useWatchlist", () => ({ useWatchlist: () => ({ watchlist: [] }) }));
vi.mock("@/lib/useProfile", () => ({
  useProfile: () => ({ profile: { username: "Operator", avatar_url: null } }),
}));
vi.mock("@/lib/IBStatusContext", () => ({ useIBStatusContext: () => ({ displayStatus: "connected" }) }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/orders",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@clerk/nextjs", () => ({
  useUser: () => ({ user: { primaryEmailAddress: { emailAddress: "operator@example.test" } } }),
  useClerk: () => ({ signOut: vi.fn() }),
  useAuth: () => ({ getToken: async () => null, isSignedIn: true }),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

/**
 * Module specifiers a file pulls eagerly into its own chunk. Type-only imports
 * are erased at build time and `dynamic(() => import(...))` is a separate
 * chunk, so neither counts. `@/components/X`, `./X` and `./mobile/X` all
 * normalise to `X`, so swapping the alias for the relative form (or back)
 * cannot dodge the assertion.
 */
function eagerImportSpecifiers(text: string): Set<string> {
  const specs = new Set<string>();
  const pattern = /^(?:import|export)\s+(?!type[\s{])[\s\S]*?\bfrom\s*["']([^"']+)["']/gm;
  for (const match of text.matchAll(pattern)) {
    const spec = match[1];
    const local = spec.startsWith("@/") || spec.startsWith(".");
    specs.add(local ? spec.split("/").pop()! : spec);
  }
  return specs;
}

describe("portfolio startup performance contracts", () => {
  it("disables automatic Next.js prefetch on every rendered shell nav link", async () => {
    const surfaces: Array<[string, React.ReactElement]> = [
      ["Sidebar", React.createElement(
        (await import("../components/Sidebar")).default,
        { activeSection: "dashboard", actionTone: "" } as never,
      )],
      ["MobileAppBar", React.createElement(
        (await import("../components/mobile/MobileAppBar")).default,
        { title: "Dashboard", onOpenSearch: () => undefined } as never,
      )],
      ["MobileMoreDrawer", React.createElement(
        (await import("../components/mobile/MobileMoreDrawer")).default,
        { open: true, onClose: () => undefined } as never,
      )],
      ["MobileTabBar", React.createElement(
        (await import("../components/mobile/MobileTabBar")).default,
        { onOpenMore: () => undefined } as never,
      )],
    ];

    for (const [name, element] of surfaces) {
      const { container, unmount } = render(element);
      const links = [...container.querySelectorAll("a[data-prefetch]")];
      expect(links.length, `${name} should render navigation links`).toBeGreaterThan(0);
      for (const link of links) {
        expect(
          link.getAttribute("data-prefetch"),
          `${name} link to ${link.getAttribute("href")} must opt out of viewport prefetch`,
        ).toBe("false");
      }
      unmount();
    }
  });

  it("keeps the portfolio surface out of the eager workspace import graph", () => {
    const specs = eagerImportSpecifiers(source("components/WorkspaceSections.tsx"));

    // Guard against a parser that silently matches nothing.
    expect(specs.has("RegimePanel"), "parser must see the workspace's real imports").toBe(true);
    expect([...specs]).not.toContain("PositionTable");
    expect([...specs]).not.toContain("PortfolioSections");
  });

  it("renders nothing for the portfolio section so the workspace never owns it", async () => {
    const WorkspaceSections = (await import("../components/WorkspaceSections")).default;
    const { container } = render(
      React.createElement(WorkspaceSections, { section: "portfolio" } as never),
    );

    expect(container.innerHTML).toBe("");
  });

  it("loads the portfolio surface from its own lazy chunk, separate from the workspace chunk", async () => {
    const { default: WorkspaceShell } = await import("../components/WorkspaceShell");
    const [portfolioModule, workspaceModule] = await Promise.all([
      import("../components/PortfolioSections"),
      import("../components/WorkspaceSections"),
    ]);
    const resolved = await Promise.all(chunkLoaders.map((load) => load()));
    const portfolioChunk = resolved.findIndex((mod) => mod.default === portfolioModule.default);
    const workspaceChunk = resolved.findIndex((mod) => mod.default === workspaceModule.default);

    expect(portfolioChunk, "PortfolioSections must sit behind next/dynamic").toBeGreaterThanOrEqual(0);
    expect(workspaceChunk, "WorkspaceSections must sit behind next/dynamic").toBeGreaterThanOrEqual(0);
    expect(portfolioChunk).not.toBe(workspaceChunk);

    const { container } = render(
      React.createElement(WorkspaceShell, { section: "portfolio" } as never),
    );

    expect(container.querySelector(`[data-testid="lazy-chunk-${portfolioChunk}"]`)).not.toBeNull();
    expect(container.querySelector(`[data-testid="lazy-chunk-${workspaceChunk}"]`)).toBeNull();

    // T-388: this render must exercise the CONNECTED branch. Without a
    // provider (or a stubbed context value) the shell reads the context
    // default `connected: false` and these timings measure the wrong path.
    expect(observedConnected.length, "shell must read useRealtimePrices()").toBeGreaterThan(0);
    expect(observedConnected.every((c) => c === true), "shell must observe connected === true").toBe(true);
  });

  /**
   * T-186: the stub answers with a real Response instead of `undefined`. A spy
   * returning undefined makes a genuine `await fetch(...)` in the RSC die on
   * `res.json()` BEFORE the no-round-trip assertion runs, so the file reds with
   * "Cannot read properties of undefined" and never names the contract it
   * guards. Answering the call lets the RSC finish and lets the assertion that
   * owns this contract be the one that fails.
   */
  const answeringFetchSpy = () => {
    const spy = vi.fn(async () => new Response("{}", {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", spy);
    return spy;
  };

  it("seeds the portfolio page from the server instead of a client fetch waterfall", async () => {
    const seed = { data: { bankroll: 42_000 }, warning: null };
    readSeedSpy.mockResolvedValue(seed);
    const fetchSpy = answeringFetchSpy();

    const page = await import("../app/portfolio/page");
    const element = await page.default();

    // Asserted FIRST: a round trip is the defect this test exists for, so it
    // must be the failure the reader sees, not a downstream seed mismatch.
    expect(fetchSpy, "the RSC must not issue its own HTTP round trip").not.toHaveBeenCalled();
    expect(page.dynamic).toBe("force-dynamic");
    expect(readSeedSpy).toHaveBeenCalledTimes(1);
    expect((element.props as { initialPortfolio?: unknown }).initialPortfolio).toBe(seed);
  });

  it("skips the RSC DB read under the authless Playwright harness", async () => {
    vi.stubEnv("RADON_AUTHLESS_TEST", "1");
    // T-186: this path was the one unstubbed `fetch` in the file. A real
    // `await fetch(...)` in the RSC escaped as live egress to localhost:3000
    // (ECONNREFUSED, or a hit on whatever is actually listening) rather than a
    // named failure. The authless path owes the same no-round-trip contract.
    const fetchSpy = answeringFetchSpy();

    const page = await import("../app/portfolio/page");
    const element = await page.default();

    expect(fetchSpy, "the authless RSC must not issue an HTTP round trip either").not.toHaveBeenCalled();
    expect(readSeedSpy).not.toHaveBeenCalled();
    expect((element.props as { initialPortfolio?: unknown }).initialPortfolio).toBeUndefined();
  });

  it("hands the server seed to usePortfolio and enriches entry dates only for orders", async () => {
    const { default: WorkspaceShell } = await import("../components/WorkspaceShell");
    const seed = { data: { bankroll: 42_000, last_sync: "2026-07-10T20:00:00.000Z" }, warning: null };

    render(React.createElement(WorkspaceShell, { section: "orders", initialPortfolio: seed } as never));
    expect(usePortfolioSpy).toHaveBeenCalledWith(
      expect.any(Boolean),
      { initialSnapshot: seed, includeEntryDates: true },
    );

    cleanup();
    usePortfolioSpy.mockClear();

    render(React.createElement(WorkspaceShell, { section: "portfolio", initialPortfolio: seed } as never));
    expect(usePortfolioSpy).toHaveBeenCalledWith(
      expect.any(Boolean),
      { initialSnapshot: seed, includeEntryDates: false },
    );
  });

  it("keeps FRED freshness in a bounded server cache", async () => {
    const csv = ["DATE,DFF", `${new Date().toISOString().slice(0, 10)},3.64`].join("\n");
    const fetchSpy = vi.fn(async () => new Response(csv, { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    const { GET } = await import("../app/api/risk-free-rate/route");
    const response = await GET();
    const body = await response.json();

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [, init] = fetchSpy.mock.calls[0] as unknown as [string, { next?: { revalidate?: number } }];
    expect(init?.next?.revalidate, "the FRED read must be revalidation-cached").toBe(86_400);
    expect(body).toMatchObject({ rate: 0.0364, source: "FRED:DFF", stale: false });
    expect(response.headers.get("Cache-Control")).toBe(`public, max-age=${init.next!.revalidate}`);
  });
});
