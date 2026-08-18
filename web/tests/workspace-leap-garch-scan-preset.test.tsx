/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WorkspaceSections from "../components/WorkspaceSections";

const replaceMock = vi.hoisted(() => vi.fn());
const searchParamsMock = vi.hoisted(() => vi.fn(() => new URLSearchParams("")));

vi.mock("next/navigation", () => ({
  usePathname: () => "/scanner",
  useRouter: () => ({ replace: replaceMock }),
  useSearchParams: searchParamsMock,
}));

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, hasMounted: true }),
}));

vi.mock("@/lib/useTickerNav", () => ({
  useTickerNav: () => ({
    navigateToTicker: vi.fn(),
  }),
}));

vi.mock("@/lib/useScanner", () => ({
  useScanner: () => ({
    data: null,
    syncing: false,
    error: null,
    lastSync: null,
    syncNow: vi.fn(),
  }),
}));

vi.mock("@/lib/useDiscover", () => ({
  useDiscover: () => ({
    data: null,
    loading: false,
    syncing: false,
    error: null,
    lastSync: null,
    syncNow: vi.fn(),
  }),
}));

vi.mock("@/lib/useThetaHarvester", () => ({
  useThetaHarvester: () => ({
    data: null,
    loading: false,
    error: null,
    lastSync: null,
    syncNow: vi.fn(),
  }),
}));

vi.mock("@/lib/useStrengthConfirmation", () => ({
  useStrengthConfirmation: () => ({
    data: null,
    loading: false,
    error: null,
    lastSync: null,
    syncNow: vi.fn(),
  }),
}));

vi.mock("@/lib/useLeap", () => ({
  useLeap: () => ({
    data: {
      scan_time: "2026-08-13T14:00:00Z",
      min_gap: 10,
      results: [],
      universe: "preset:largecaps",
    },
    loading: false,
    syncing: false,
    error: null,
    lastSync: "2026-08-13T14:00:00Z",
    syncNow: vi.fn(),
  }),
}));

vi.mock("@/lib/useGarchConvergence", () => ({
  useGarchConvergence: () => ({
    data: {
      scan_time: "2026-08-13T14:00:00Z",
      tickers: {},
      pairs: [],
      universe: "preset:largecaps",
    },
    loading: false,
    syncing: false,
    error: null,
    lastSync: "2026-08-13T14:00:00Z",
    syncNow: vi.fn(),
  }),
}));

vi.mock("@/lib/useVolCone", () => ({
  useVolCone: () => ({
    data: null,
    loading: false,
    error: null,
    lastSync: null,
    syncNow: vi.fn(),
  }),
}));

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({
    ok: true,
    json: async () => ({}),
    text: async () => "",
  });
  vi.stubGlobal("fetch", fetchMock);
  searchParamsMock.mockReturnValue(new URLSearchParams(""));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function postedBody(url: string): unknown {
  const call = fetchMock.mock.calls.find(
    ([requested, init]) =>
      String(requested) === url && (init as RequestInit | undefined)?.method === "POST",
  );
  expect(call).toBeTruthy();
  return JSON.parse(String((call?.[1] as RequestInit).body));
}

describe("WorkspaceSections SCAN default preset", () => {
  it("posts {preset: largecaps} for LEAP when no custom tickers", async () => {
    searchParamsMock.mockReturnValue(new URLSearchParams("mode=leap"));
    render(<WorkspaceSections section="scanner" />);

    const section = screen.getByTestId("leap-scanner-section");
    fireEvent.click(within(section).getByRole("button", { name: "SCAN" }));

    await waitFor(() => {
      expect(postedBody("/api/leap/scan")).toEqual({ preset: "largecaps" });
    });
  });

  it("posts {preset: largecaps} for GARCH when no custom tickers", async () => {
    searchParamsMock.mockReturnValue(new URLSearchParams("mode=garch"));
    render(<WorkspaceSections section="scanner" />);

    const section = screen.getByTestId("garch-scanner-section");
    fireEvent.click(within(section).getByRole("button", { name: "SCAN" }));

    await waitFor(() => {
      expect(postedBody("/api/garch-convergence/scan")).toEqual({ preset: "largecaps" });
    });
  });
});
