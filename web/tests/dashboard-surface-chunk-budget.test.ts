/**
 * @vitest-environment jsdom
 *
 * PERF-042: DashboardSurface was a static import in WorkspaceShell, so every
 * workspace route paid its eager graph (newsfeed, scanners, catalysts, engine,
 * markdown, share, 73 modules / 430304 source bytes measured 2026-11-02).
 * Portfolio, performance and the omnibus workspace already sit behind
 * next/dynamic; dashboard must too.
 */
import React from "react";
import { readFileSync, existsSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.setConfig({ testTimeout: 20_000, hookTimeout: 20_000 });

const WEB = join(import.meta.dirname, "..");
const source = (path: string) => readFileSync(join(WEB, path), "utf8");

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

vi.mock("@/lib/usePortfolio", () => ({
  usePortfolio: () => ({
    data: null, loading: false, syncing: false, error: null, lastSync: null, syncNow: () => {},
  }),
}));
const stub = (testId: string) => ({
  default: () => React.createElement("div", { "data-testid": testId }),
});
vi.mock("@/components/Header", () => stub("header"));
vi.mock("@/components/MetricCards", () => stub("metric-cards"));
vi.mock("@/components/ChatLauncher", () => stub("chat-launcher"));
vi.mock("@/components/DemoWelcomeModal", () => stub("demo-welcome"));
vi.mock("@/components/mobile/MobileShell", () => stub("mobile-shell"));
vi.mock("@/components/FooterTelemetryStrip", () => stub("footer-telemetry"));
vi.mock("@/components/FuturesStrip", () => stub("futures-strip"));
vi.mock("@/components/OfflineBanner", () => stub("offline-banner"));
vi.mock("@/components/Toast", () => stub("toasts"));
vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActions: () => ({ drainNotifications: () => [], setOrdersUpdater: () => {} }),
}));
const tickerDetailStub = new Proxy(
  { chainContracts: [], depthSymbol: null, depthSymbols: [], depthFutureExpiry: null } as Record<string, unknown>,
  { get: (target, key: string) => (key in target ? target[key] : () => {}) },
);
vi.mock("@/lib/TickerDetailContext", () => ({ useTickerDetail: () => tickerDetailStub }));
vi.mock("@/lib/offline/OfflineStatusContext", () => ({ useOfflineStatus: () => ({ offline: false }) }));
vi.mock("@/lib/ThemeContext", () => ({ useTheme: () => ({ theme: "dark", toggleTheme: () => {} }) }));
vi.mock("@/lib/RealtimePricesContext", () => ({
  useRealtimePrices: () => ({
    prices: {}, fundamentals: {}, depths: {}, tape: {},
    connected: true, ibConnected: true, ibIssue: null, ibStatusMessage: null,
    error: null, reconnect: () => {}, getSnapshot: async () => ({}), publishSubscriptions: () => {},
  }),
}));
vi.mock("@/lib/useOrders", () => ({
  useOrders: () => ({ data: null, loading: false, syncing: false, error: null, lastSync: null, syncNow: () => {} }),
}));
vi.mock("@/lib/useWatchlist", () => ({ useWatchlist: () => ({ watchlist: [] }) }));
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
});

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

const FROM_RE = /^(?:import|export)\s+(?!type[\s{])[\s\S]*?\bfrom\s*["']([^"']+)["']/gm;
const SIDE_RE = /^import\s+["']([^"']+)["']/gm;

function resolveLocal(fromFile: string, spec: string): string | null {
  let base: string;
  if (spec.startsWith("@/")) base = join(WEB, spec.slice(2));
  else if (spec.startsWith(".")) base = resolve(dirname(fromFile), spec);
  else return null;
  const candidates = [
    base,
    `${base}.tsx`,
    `${base}.ts`,
    `${base}.jsx`,
    `${base}.js`,
    `${base}.css`,
    `${base}.module.css`,
    join(base, "index.ts"),
    join(base, "index.tsx"),
  ];
  return candidates.find((c) => existsSync(c)) ?? null;
}

function walkEager(entryRel: string, skipNames: Set<string> = new Set()): string[] {
  const files: string[] = [];
  const seen = new Set<string>();
  const stack = [resolve(WEB, entryRel)];
  while (stack.length) {
    const file = stack.pop()!;
    if (seen.has(file)) continue;
    seen.add(file);
    const name = file.split("/").pop()!;
    if (skipNames.has(name)) continue;
    files.push(file);
    let text: string;
    try {
      text = readFileSync(file, "utf8");
    } catch {
      continue;
    }
    const specs = [...text.matchAll(FROM_RE), ...text.matchAll(SIDE_RE)].map((m) => m[1]);
    for (const spec of specs) {
      const resolved = resolveLocal(file, spec);
      if (resolved) stack.push(resolved);
    }
  }
  return files;
}

function bytesOf(files: string[]): number {
  return files.reduce((sum, f) => sum + statSync(f).size, 0);
}

describe("PERF-042 dashboard surface chunk budget", () => {
  it("keeps DashboardSurface out of the eager workspace-shell import graph", () => {
    const specs = eagerImportSpecifiers(source("components/WorkspaceShell.tsx"));
    expect(specs.has("InstrumentSkeleton"), "parser must see the shell's real imports").toBe(true);
    expect([...specs]).not.toContain("DashboardSurface");
    expect([...specs]).not.toContain("DashboardSurface.tsx");
  });

  it("loads the dashboard surface from its own lazy chunk, separate from portfolio and workspace", async () => {
    const { default: WorkspaceShell } = await import("../components/WorkspaceShell");
    const [dashboardModule, portfolioModule, workspaceModule] = await Promise.all([
      import("../components/dashboard/DashboardSurface"),
      import("../components/PortfolioSections"),
      import("../components/WorkspaceSections"),
    ]);
    const resolved = await Promise.all(chunkLoaders.map((load) => load()));
    const dashboardChunk = resolved.findIndex((mod) => mod.default === dashboardModule.default);
    const portfolioChunk = resolved.findIndex((mod) => mod.default === portfolioModule.default);
    const workspaceChunk = resolved.findIndex((mod) => mod.default === workspaceModule.default);

    expect(dashboardChunk, "DashboardSurface must sit behind next/dynamic").toBeGreaterThanOrEqual(0);
    expect(portfolioChunk).toBeGreaterThanOrEqual(0);
    expect(workspaceChunk).toBeGreaterThanOrEqual(0);
    expect(dashboardChunk).not.toBe(portfolioChunk);
    expect(dashboardChunk).not.toBe(workspaceChunk);

    const orders = render(React.createElement(WorkspaceShell, { section: "orders" } as never));
    expect(orders.container.querySelector(`[data-testid="lazy-chunk-${dashboardChunk}"]`)).toBeNull();
    orders.unmount();

    const dashboard = render(React.createElement(WorkspaceShell, { section: "dashboard" } as never));
    expect(dashboard.container.querySelector(`[data-testid="lazy-chunk-${dashboardChunk}"]`)).not.toBeNull();
    expect(dashboard.container.querySelector(`[data-testid="lazy-chunk-${portfolioChunk}"]`)).toBeNull();
    expect(dashboard.container.querySelector(`[data-testid="lazy-chunk-${workspaceChunk}"]`)).toBeNull();
    dashboard.unmount();
  });

  it("pays zero source bytes of the dashboard graph on the eager shell path", () => {
    const full = walkEager("components/WorkspaceShell.tsx");
    const withoutDashboard = walkEager("components/WorkspaceShell.tsx", new Set(["DashboardSurface.tsx"]));
    const pulled = full.filter((f) => !withoutDashboard.includes(f));
    // Before: 73 modules / 430304 source bytes (2026-11-02 import-graph walk).
    expect(pulled.length, "static DashboardSurface import must not pull dashboard modules into the shell chunk").toBe(0);
    expect(bytesOf(pulled)).toBe(0);
  });
});
