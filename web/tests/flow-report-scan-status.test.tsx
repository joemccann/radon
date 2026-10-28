/**
 * @vitest-environment jsdom
 *
 * 2026-10-27: QQQ's flow scans were killed at FastAPI's deadline for four
 * months. Nothing reached the page: it said "Scan still running" for five
 * minutes of polling, then fell back to a 2026-06-23 cache. FastAPI now writes
 * `<TICKER>.scan.json`; the GET route passes it through as `scan_status`, and
 * the hook ends the poll with the real reason as soon as the scan it started
 * has failed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

const fsMocks = vi.hoisted(() => ({ readFile: vi.fn() }));
vi.mock("fs/promises", async (importOriginal) => {
  const actual = await importOriginal<typeof import("fs/promises")>();
  return { ...actual, default: { ...actual, readFile: fsMocks.readFile }, readFile: fsMocks.readFile };
});
vi.mock("fs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("fs")>();
  const statSync = vi.fn(() => ({ mtime: new Date("2026-06-23T15:32:15Z") }));
  return { ...actual, default: { ...actual, statSync }, statSync };
});
vi.mock("@/lib/radonApi", () => ({ radonFetch: vi.fn() }));

import { GET } from "@/app/api/flow-analysis/[ticker]/route";
import { useTickerFlowReport } from "@/lib/useTickerFlowReport";

const OLD_REPORT = {
  ticker: "QQQ",
  fetched_at: "2026-06-23T15:32:00Z",
  verdict: { direction: "BULLISH" as const, confidence: 30 },
  cache_meta: { last_refresh: "2026-06-23T15:32:15.000Z", age_seconds: 10_886_400, is_stale: true },
};

function response(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, headers: new Headers(), json: async () => body } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  fsMocks.readFile.mockReset();
});

describe("GET /api/flow-analysis/[ticker] carries the scan status", () => {
  it("passes <TICKER>.scan.json through as scan_status", async () => {
    const status = { state: "failed", started_at: "2026-10-27T16:27:29Z", finished_at: "2026-10-27T16:31:14Z", error: "timed out after 225s" };
    fsMocks.readFile.mockImplementation(async (path: string) => {
      if (String(path).endsWith("QQQ.scan.json")) return JSON.stringify(status);
      return JSON.stringify(OLD_REPORT);
    });
    const res = await GET(new Request("http://localhost/api/flow-analysis/QQQ"), { params: Promise.resolve({ ticker: "QQQ" }) });
    const body = await res.json();
    expect(body.scan_status).toEqual(status);
    expect(body.verdict.direction).toBe("BULLISH");
  });

  it("omits scan_status when no scan has recorded one", async () => {
    fsMocks.readFile.mockImplementation(async (path: string) => {
      if (String(path).endsWith(".scan.json")) throw Object.assign(new Error("ENOENT"), { code: "ENOENT" });
      return JSON.stringify(OLD_REPORT);
    });
    const res = await GET(new Request("http://localhost/api/flow-analysis/QQQ"), { params: Promise.resolve({ ticker: "QQQ" }) });
    const body = await res.json();
    expect(body.scan_status).toBeUndefined();
  });
});

describe("useTickerFlowReport ends the poll when the scan it started failed", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-27T16:27:30Z"));
  });

  it("reports the failure instead of polling the full window", async () => {
    let polls = 0;
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return response(202, { ...OLD_REPORT, scan_pending: true });
      polls += 1;
      if (polls < 3) return response(200, { ...OLD_REPORT, scan_status: { state: "running", started_at: "2026-10-27T16:27:29Z", finished_at: null, error: null } });
      return response(200, {
        ...OLD_REPORT,
        scan_status: { state: "failed", started_at: "2026-10-27T16:27:29Z", finished_at: "2026-10-27T16:31:14Z", error: "Script flow_report.py timed out after 225s" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTickerFlowReport("QQQ", { pendingPollMs: 5, pendingPollLimit: 60 }));
    await waitFor(() => expect(result.current.status).toBe("stale"));
    // 1 initial GET + 2 polls: the failure ends it, not the 60-poll bound.
    expect(polls).toBe(3);
    expect(result.current.error).toMatch(/ran out of time/i);
    expect(result.current.error).toMatch(/last cached report/i);
    expect(result.current.data?.verdict?.direction).toBe("BULLISH");
  });

  it("ignores a failure recorded by an earlier scan", async () => {
    let landed = false;
    const earlier = { state: "failed", started_at: "2026-10-26T10:00:00Z", finished_at: "2026-10-26T10:03:45Z", error: "old" };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return response(202, { ...OLD_REPORT, scan_pending: true });
      if (landed) {
        return response(200, {
          ...OLD_REPORT,
          verdict: { direction: "BEARISH", confidence: 55 },
          cache_meta: { last_refresh: "2026-10-27T16:28:40.000Z", age_seconds: 5, is_stale: false },
        });
      }
      return response(200, { ...OLD_REPORT, scan_status: earlier });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTickerFlowReport("QQQ", { pendingPollMs: 5 }));
    await waitFor(() => expect(result.current.status).toBe("pending"));
    landed = true;
    await waitFor(() => expect(result.current.status).toBe("fresh"));
    expect(result.current.data?.verdict?.direction).toBe("BEARISH");
  });
});
