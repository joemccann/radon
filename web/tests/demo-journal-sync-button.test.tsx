/**
 * @vitest-environment jsdom
 *
 * The Journal "SYNC IB" button imports unreconciled IB executions through the
 * operatorOnly POST /api/journal/sync. The demo has no IB account, so the
 * control is hidden there; the operator keeps it.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("@/lib/useJournal", () => ({
  useJournal: () => ({
    data: { trades: [] },
    loading: false,
    error: null,
    syncWithIB: vi.fn(),
    syncing: false,
    lastSyncResult: null,
    refresh: vi.fn(),
  }),
}));
vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, hasMounted: true, width: 1200 }),
}));
vi.mock("@/lib/useTickerNav", () => ({ useTickerNav: () => ({ navigateToTicker: vi.fn() }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/journal",
  useSearchParams: () => new URLSearchParams(),
}));

import WorkspaceSections from "../components/WorkspaceSections";

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe("Journal SYNC IB control", () => {
  it("is hidden on the demo deployment", () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
    render(React.createElement(WorkspaceSections, { section: "journal" }));
    expect(screen.getByTestId("journal-section")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /SYNC IB/ })).toBeNull();
  });

  it("is shown to the operator", () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "");
    render(React.createElement(WorkspaceSections, { section: "journal" }));
    expect(screen.getByRole("button", { name: /SYNC IB/ })).toBeTruthy();
  });
});
