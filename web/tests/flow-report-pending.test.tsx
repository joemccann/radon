/**
 * @vitest-environment jsdom
 *
 * REL-164 (R-464, R-465): a flow report in flight is `pending`, not
 * `unavailable`, and a served cache that predates the session close is dated.
 *
 * R-464: FastAPI detaches the scan from the request (8db918a5) and the Next
 * route gives up waiting at 25s by design, yet that exit ran through the
 * failure branch: `X-Sync-Warning: Radon API unavailable`, `is_stale: true`,
 * and the hook set status `stale` with a fault banner while FastAPI was fine
 * and `_scan_and_cache` was still running. Nothing polled for the landing.
 *
 * R-465: the after-hours TTL was 8h of wall clock, so a 09:00 ET report viewed
 * at 16:30 ET passed as fresh and rendered the full verdict with no age.
 */
import React from "react";
import { cleanup, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useTickerFlowReport } from "@/lib/useTickerFlowReport";

const PRE_MARKET_SCAN = {
  ticker: "AAPL",
  fetched_at: "2026-05-08T13:00:00Z", // 09:00 ET Friday
  verdict: { direction: "BULLISH" as const, confidence: 71, rationale: "Sustained DP buying" },
  dark_pool: { aggregate: { flow_direction: "ACCUMULATION", flow_strength: 60, dp_buy_ratio: 0.61, num_prints: 900 } },
  cache_meta: { last_refresh: "2026-05-08T13:00:05.000Z", age_seconds: 27_000, is_stale: true },
};

const LANDED_SCAN = {
  ...PRE_MARKET_SCAN,
  fetched_at: "2026-05-08T20:31:00Z",
  verdict: { direction: "BEARISH" as const, confidence: 64, rationale: "Session distribution" },
  cache_meta: { last_refresh: "2026-05-08T20:31:04.000Z", age_seconds: 5, is_stale: false },
};

function response(status: number, body: unknown, headers: Record<string, string> = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(headers),
    json: async () => body,
  } as unknown as Response;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useTickerFlowReport — a 202 is a scan still running (R-464)", () => {
  beforeEach(() => {
    // 16:30 ET Friday: the session has closed and the 09:00 ET scan predates it.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-05-08T20:30:00Z"));
  });

  it("enters pending with the cached report and no banner, then lands on the next last_refresh", async () => {
    let landed = false;
    const posts: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        posts.push(String(input));
        return response(202, { ...PRE_MARKET_SCAN, scan_pending: true });
      }
      // The pre-close cache until the detached scan writes the new file.
      return response(200, landed ? LANDED_SCAN : PRE_MARKET_SCAN);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTickerFlowReport("AAPL", { pendingPollMs: 10 }));

    await waitFor(() => expect(result.current.status).toBe("pending"));
    expect(posts).toEqual(["/api/flow-analysis/AAPL"]);
    expect(result.current.error).toBeNull();
    expect(result.current.data?.verdict?.direction).toBe("BULLISH");

    landed = true;
    await waitFor(() => expect(result.current.status).toBe("fresh"));
    expect(result.current.data?.verdict?.direction).toBe("BEARISH");
    expect(result.current.data?.cache_meta?.last_refresh).toBe("2026-05-08T20:31:04.000Z");
    // One scan per load: the landing is observed through GET, never re-POSTed.
    expect(posts).toHaveLength(1);
  });

  it("a first-scan ticker is pending with no data, not a 502 error", async () => {
    let landed = false;
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return response(202, { ticker: "AAPL", scan_pending: true, cache_meta: { last_refresh: null } });
      }
      if (landed) return response(200, LANDED_SCAN);
      return response(200, { ticker: "AAPL", missing: true, cache_meta: { last_refresh: null } });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTickerFlowReport("AAPL", { pendingPollMs: 10 }));

    await waitFor(() => expect(result.current.status).toBe("pending"));
    expect(result.current.data).toBeNull();
    expect(result.current.error).toBeNull();

    landed = true;
    await waitFor(() => expect(result.current.status).toBe("fresh"));
    expect(result.current.data?.verdict?.direction).toBe("BEARISH");
  });

  it("gives up polling after the bound and serves the cache as stale, dated", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return response(202, { ...PRE_MARKET_SCAN, scan_pending: true });
      return response(200, PRE_MARKET_SCAN);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useTickerFlowReport("AAPL", { pendingPollMs: 5, pendingPollLimit: 3 }),
    );

    await waitFor(() => expect(result.current.status).toBe("stale"));
    expect(result.current.error).toMatch(/still running/i);
    expect(result.current.data?.verdict?.direction).toBe("BULLISH");
  });
});

/* ── The component: pending shows the last good scan's age, no fault banner ── */

const hookState = vi.hoisted(() => ({
  data: null as unknown,
  status: "pending" as string,
  error: null as string | null,
  refresh: () => {},
}));

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, isTablet: false, hasMounted: true }),
}));

describe("TickerFlowReport — pending is dated, not faulted (R-464 / R-465)", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-05-08T20:30:00Z"));
    vi.doMock("@/lib/useTickerFlowReport", () => ({ useTickerFlowReport: () => hookState }));
  });

  afterEach(() => {
    vi.doUnmock("@/lib/useTickerFlowReport");
    vi.resetModules();
  });

  async function renderWith(state: Record<string, unknown>) {
    Object.assign(hookState, state);
    vi.resetModules();
    const { default: TickerFlowReport } = await import("../components/flow-analysis/TickerFlowReport");
    return render(<TickerFlowReport ticker="AAPL" />);
  }

  it("dates the pre-close report it is still showing while the scan runs", async () => {
    await renderWith({ data: PRE_MARKET_SCAN, status: "pending", error: null });
    expect(screen.getByTestId("flow-stale-age").textContent).toContain("2026-05-08 · today");
    expect(screen.getByTestId("flow-hero-stale").textContent).toContain("LAST GOOD SCAN");
    expect(screen.getByTestId("flow-hero-pending").textContent).toMatch(/still running/i);
    expect(screen.queryByText(/API unavailable/i)).toBeNull();
    expect(screen.getByRole("status").getAttribute("data-status")).toBe("pending");
  });

  it("keeps the Refresh control parked while the server scan is running", async () => {
    await renderWith({ data: PRE_MARKET_SCAN, status: "pending", error: null });
    expect((screen.getByLabelText("Refresh flow report") as HTMLButtonElement).disabled).toBe(true);
  });

  it("a first-scan ticker shows the analyzing panel while pending, no error strip", async () => {
    await renderWith({ data: null, status: "pending", error: null });
    expect(screen.getByText(/Sampling AAPL flow/i)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
