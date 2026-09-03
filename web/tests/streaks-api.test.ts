/** @vitest-environment node */
/**
 * GET /api/streaks — thin authenticated proxy to FastAPI /streaks/{ticker}.
 * Pins: symbol bounding, exact upstream path + timeout, per-symbol
 * single-flight, 502 with scrubbed detail when FastAPI is down.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const mocks = vi.hoisted(() => ({
  radonFetch: vi.fn(),
  requireRouteAccess: vi.fn(),
}));

vi.mock("@/lib/radonApi", () => ({
  radonFetch: mocks.radonFetch,
  RadonApiError: class RadonApiError extends Error {
    status: number;
    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
    }
  },
}));

vi.mock("@/lib/routeAccess", () => ({
  requireRouteAccess: mocks.requireRouteAccess,
}));

function payload(symbol: string) {
  return {
    symbol,
    scan_time: "2026-08-30T21:00:00+00:00",
    source: "uw",
    missing: false,
    count: 2,
    first_date: "2026-08-27",
    last_date: "2026-08-28",
    current: { date: "2026-08-28", close: 101, streak: 1, day_change_pct: 1 },
    stats: {
      max_streak: 1,
      max_streak_end: "2026-08-28",
      runs_total: 1,
      runs_ge_current: 1,
      avg_run: 1,
      up_day_pct: 100,
    },
    series: [
      { date: "2026-08-27", close: 100, streak: 0 },
      { date: "2026-08-28", close: 101, streak: 1 },
    ],
  };
}

async function importRoute() {
  return import("../app/api/streaks/route");
}

beforeEach(() => {
  vi.resetModules();
  mocks.radonFetch.mockReset();
  mocks.requireRouteAccess.mockReset();
  mocks.requireRouteAccess.mockResolvedValue({ ok: true });
});

describe("GET /api/streaks", () => {
  it("exports read capability and rejects a missing symbol without calling FastAPI", async () => {
    const route = await importRoute();
    expect(route.radonCapability).toBe("read.spawn"); // REL-177 (R-491): spawn-capped

    const res = await route.GET(new Request("http://localhost/api/streaks"));
    expect(res.status).toBe(400);
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("rejects an out-of-bounds symbol", async () => {
    const route = await importRoute();
    const res = await route.GET(
      new Request("http://localhost/api/streaks?symbol=BAD%24SYM"),
    );
    expect(res.status).toBe(400);
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("uppercases the symbol and proxies the exact FastAPI path with a 60s budget", async () => {
    mocks.radonFetch.mockResolvedValueOnce(payload("SPY"));
    const route = await importRoute();

    const res = await route.GET(
      new Request("http://localhost/api/streaks?symbol=spy"),
    );

    expect(res.status).toBe(200);
    expect(mocks.radonFetch).toHaveBeenCalledWith("/streaks/SPY", {
      timeout: 60_000,
    });
    const body = await res.json();
    expect(body.symbol).toBe("SPY");
    expect(body.series).toHaveLength(2);
  });

  it("coalesces concurrent requests for the same symbol into one upstream call", async () => {
    let release: (value: unknown) => void = () => {};
    mocks.radonFetch.mockImplementationOnce(
      () => new Promise((resolve) => { release = resolve; }),
    );
    const route = await importRoute();

    const first = route.GET(new Request("http://localhost/api/streaks?symbol=SPY"));
    const second = route.GET(new Request("http://localhost/api/streaks?symbol=SPY"));
    // Let both handlers pass their access gate and reach the in-flight map
    // before the upstream promise resolves.
    await new Promise((resolve) => setTimeout(resolve, 0));
    release(payload("SPY"));
    const [a, b] = await Promise.all([first, second]);

    expect(mocks.radonFetch).toHaveBeenCalledTimes(1);
    expect(a.status).toBe(200);
    expect(b.status).toBe(200);
  });

  it("does not coalesce different symbols", async () => {
    mocks.radonFetch
      .mockResolvedValueOnce(payload("SPY"))
      .mockResolvedValueOnce(payload("QQQ"));
    const route = await importRoute();

    await Promise.all([
      route.GET(new Request("http://localhost/api/streaks?symbol=SPY")),
      route.GET(new Request("http://localhost/api/streaks?symbol=QQQ")),
    ]);

    expect(mocks.radonFetch).toHaveBeenCalledTimes(2);
    expect(mocks.radonFetch).toHaveBeenCalledWith("/streaks/SPY", { timeout: 60_000 });
    expect(mocks.radonFetch).toHaveBeenCalledWith("/streaks/QQQ", { timeout: 60_000 });
  });

  it("returns 502 with a scrubbed detail when FastAPI is unreachable", async () => {
    mocks.radonFetch.mockRejectedValueOnce(new Error("connect ECONNREFUSED 127.0.0.1:8321"));
    const route = await importRoute();

    const res = await route.GET(
      new Request("http://localhost/api/streaks?symbol=SPY"),
    );

    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.error).toBe("Failed to fetch streaks");
    expect(typeof body.detail).toBe("string");
  });

  it("retries upstream after a failed flight instead of caching the rejection", async () => {
    mocks.radonFetch
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(payload("SPY"));
    const route = await importRoute();

    const failed = await route.GET(new Request("http://localhost/api/streaks?symbol=SPY"));
    expect(failed.status).toBe(502);

    const ok = await route.GET(new Request("http://localhost/api/streaks?symbol=SPY"));
    expect(ok.status).toBe(200);
    expect(mocks.radonFetch).toHaveBeenCalledTimes(2);
  });
});
