/**
 * @vitest-environment jsdom
 *
 * T-107: the route-change re-fetch feature was only tested through a
 * hand-supplied RouteRefreshContext value. Every consumer bails on the
 * context's "" default, so deleting the usePathname() wiring in
 * RouteRefreshProvider, or dropping the provider from <Providers>, stayed
 * green. These cases drive the REAL provider (and the real Providers tree)
 * through a mocked next/navigation pathname.
 */

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { PortfolioData } from "../lib/types";

// The real <Providers> tree collapses to ThemeProvider-only in first-run
// setup mode (no Clerk publishable key). This test drives the FULL tree, so
// pin a key before the module-scope read in components/Providers.tsx runs.
vi.hoisted(() => {
  process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY =
    process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY || "pk_test_wiring_stub";
});

const nav = vi.hoisted(() => ({ pathname: "/dashboard" }));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
}));

vi.mock("@clerk/nextjs", () => ({
  ClerkProvider: ({ children }: { children: ReactNode }) => children,
  useAuth: () => ({
    isLoaded: true,
    isSignedIn: true,
    userId: "user_test",
    getToken: async () => null,
  }),
}));

vi.mock("@clerk/themes", () => ({ dark: {} }));

import { RouteRefreshProvider } from "../lib/RouteRefreshContext";
import Providers from "../components/Providers";
import { usePortfolio } from "../lib/usePortfolio";

const portfolio: PortfolioData = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-08-17T14:00:00Z",
  positions: [],
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function pathOf(call: unknown[]): string {
  return new URL(String(call[0]), "http://localhost:3000").pathname;
}

function methodOf(call: unknown[]): string {
  const options = (call[1] ?? {}) as RequestInit;
  return (options.method ?? "GET").toUpperCase();
}

function portfolioCalls(fetchMock: ReturnType<typeof vi.fn>): string[] {
  return fetchMock.mock.calls
    .filter((call) => pathOf(call) === "/api/portfolio")
    .map(methodOf);
}

function installFetchMock() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), "http://localhost:3000").pathname;
    if (path === "/api/portfolio") return jsonResponse(portfolio);
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

class InertWebSocket {
  static CONNECTING = 0 as const;
  static OPEN = 1 as const;
  static CLOSING = 2 as const;
  static CLOSED = 3 as const;
  readyState = InertWebSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  url: string;
  constructor(url: string) {
    this.url = url;
  }
  send() {}
  close() {
    this.readyState = InertWebSocket.CLOSED;
  }
}

function installMatchMediaShim() {
  if (typeof window.matchMedia === "function") return;
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: () => ({
      matches: false,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
    }),
  });
}

function RealProviderWrapper({ children }: { children: ReactNode }) {
  return <RouteRefreshProvider>{children}</RouteRefreshProvider>;
}

function AppProvidersWrapper({ children }: { children: ReactNode }) {
  return <Providers>{children}</Providers>;
}

describe("RouteRefreshProvider wiring (T-107)", () => {
  beforeEach(() => {
    nav.pathname = "/dashboard";
    installMatchMediaShim();
    vi.stubGlobal("WebSocket", InertWebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("re-GETs portfolio when usePathname() changes under the real RouteRefreshProvider", async () => {
    const fetchMock = installFetchMock();

    const { result, rerender } = renderHook(() => usePortfolio(true), {
      wrapper: RealProviderWrapper,
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(portfolioCalls(fetchMock)).toEqual(["GET"]);

    nav.pathname = "/portfolio";
    rerender();

    await waitFor(() => expect(portfolioCalls(fetchMock)).toEqual(["GET", "GET"]));
  });

  it("re-GETs portfolio when usePathname() changes under the app <Providers> tree", async () => {
    const fetchMock = installFetchMock();

    const { result, rerender } = renderHook(() => usePortfolio(true), {
      wrapper: AppProvidersWrapper,
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(portfolioCalls(fetchMock)).toEqual(["GET"]);

    nav.pathname = "/portfolio";
    rerender();

    await waitFor(() => expect(portfolioCalls(fetchMock)).toEqual(["GET", "GET"]));
  });
});
