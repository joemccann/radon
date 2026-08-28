/**
 * @vitest-environment jsdom
 *
 * R-349 / R-350 / R-356 / REL-128.
 *
 * R-349: the effect's dep array read `triggerRef.current` — a REF — behind an
 * `exhaustive-deps` suppression, while `refresh` both mutated that ref and
 * called `load` directly. One Refresh click therefore issued TWO independent
 * load cycles: `refresh`'s `load(A)` set state synchronously before its first
 * await, and the resulting re-render made the effect's dep compare 1 against
 * the captured 0, aborting the in-flight controller and firing `load` again.
 * A client abort does not stop an already-spawned FastAPI subprocess, so two
 * 300s `flow_report.py` runs occupied the general lane — the operator's own
 * Refresh manufacturing the capacity shed. CLAUDE.md names this dep-array
 * shape as the class that shipped the stale-acknowledgement Transmit bug.
 *
 * R-350: the POST success path accepted any 200 as a completed scan without
 * checking `is_stale` or `X-Sync-Warning`, which the route sets when its own
 * 130s timeout fires or a shed persists. A scan that never completed rendered
 * as a FRESH directional verdict with no banner.
 */
import React from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useTickerFlowReport } from "@/lib/useTickerFlowReport";
import { flowReportErrorCopy, isExhaustedCapacityError } from "@/lib/flowReportError";

const FRESH = {
  ticker: "AAPL",
  fetched_at: new Date().toISOString(),
  verdict: { direction: "BULLISH" as const, confidence: 80, rationale: "r" },
};

function jsonResponse(body: unknown, headers: Record<string, string> = {}) {
  return {
    ok: true,
    status: 200,
    headers: new Headers(headers),
    json: async () => body,
  } as unknown as Response;
}

afterEach(cleanup);

describe("useTickerFlowReport — one Refresh is one scan (R-349)", () => {
  it("issues exactly one POST per Refresh click", async () => {
    const posts: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") {
        posts.push(url);
        return jsonResponse(FRESH);
      }
      // GET: a stale cache would itself trigger a scan, so serve fresh.
      return jsonResponse(FRESH);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTickerFlowReport("AAPL"));
    await waitFor(() => expect(result.current.status).toBe("fresh"));
    expect(posts).toHaveLength(0);

    await act(async () => {
      result.current.refresh();
    });
    await waitFor(() => expect(result.current.status).toBe("fresh"));

    // One click. The GET runs again (that is the load cycle), but only ONE
    // cycle may run: two cycles is what pinned the general lane.
    const getCalls = fetchMock.mock.calls.filter(([, init]) => init?.method !== "POST");
    expect(getCalls).toHaveLength(2);
  });

  it("carries no exhaustive-deps suppression on the load effect", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const here = resolve(fileURLToPath(import.meta.url), "..");
    const src = readFileSync(resolve(here, "..", "lib", "useTickerFlowReport.ts"), "utf8");
    expect(src).not.toContain("eslint-disable-next-line react-hooks/exhaustive-deps");
    expect(src).not.toContain("triggerRef.current]");
  });
});

describe("useTickerFlowReport — a degraded 200 is not a scan (R-350)", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("does not report fresh for an is_stale POST body", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return jsonResponse({ ...FRESH, is_stale: true });
      }
      return jsonResponse({ ticker: "AAPL", missing: true });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTickerFlowReport("AAPL"));
    await waitFor(() => expect(result.current.status).not.toBe("scanning"));
    expect(result.current.status).toBe("stale");
    expect(result.current.error).toBeTruthy();
  });

  it("does not report fresh when X-Sync-Warning is set", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return jsonResponse(FRESH, { "X-Sync-Warning": "upstream timeout" });
      }
      return jsonResponse({ ticker: "AAPL", missing: true });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTickerFlowReport("AAPL"));
    await waitFor(() => expect(result.current.status).not.toBe("scanning"));
    expect(result.current.status).toBe("stale");
  });

  it("still reports fresh for a genuinely completed scan", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return jsonResponse(FRESH);
      return jsonResponse({ ticker: "AAPL", missing: true });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTickerFlowReport("AAPL"));
    await waitFor(() => expect(result.current.status).toBe("fresh"));
    expect(result.current.error).toBeNull();
  });
});

describe("flowReportErrorCopy — an exhausted shed reads differently (R-356)", () => {
  it("distinguishes a shed the server retried through", () => {
    const first = "subprocess capacity exhausted";
    const exhausted = "subprocess capacity exhausted: still shed after 3 attempts";

    expect(isExhaustedCapacityError(first)).toBe(false);
    expect(isExhaustedCapacityError(exhausted)).toBe(true);
    expect(flowReportErrorCopy(first)).not.toBe(flowReportErrorCopy(exhausted));
    expect(flowReportErrorCopy(first)).toContain("Wait a moment");
    expect(flowReportErrorCopy(exhausted)).toContain("capacity incident");
    expect(flowReportErrorCopy(exhausted)).not.toContain("Wait a moment");
  });

  it("surfaces Retry-After when the message carries it", () => {
    expect(flowReportErrorCopy("Too Many Requests retry-after: 30")).toContain("30s");
  });
});
