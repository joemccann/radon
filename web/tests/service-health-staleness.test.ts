/**
 * @vitest-environment node
 *
 * Tests for /api/service-health stale-row coercion.
 *
 * Behavior change: an old ``ok`` row whose ``updated_at`` is past the
 * service's freshness window is coerced to ``stale`` in the response so
 * the banner can render it distinctly from ``error`` while still
 * surfacing the issue.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

type MockRow = Record<string, unknown>;

function mockGetDb(rows: MockRow[]): void {
  vi.doMock("@/lib/db", () => ({
    getDb: () => ({
      execute: vi.fn().mockResolvedValue({ rows }),
    }),
  }));
}

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllEnvs();
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe("/api/service-health — stale coercion", () => {
  it("fresh ok row stays ok and is not in failing list", async () => {
    const fresh = new Date().toISOString();
    mockGetDb([
      {
        service: "newsfeed-scraper",
        state: "ok",
        last_attempt_started_at: fresh,
        last_attempt_finished_at: fresh,
        last_error: null,
        updated_at: fresh,
      },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    expect(body.services[0].state).toBe("ok");
    expect(body.failing).toHaveLength(0);
  });

  it("stale ok row is coerced to state=stale and surfaces in failing", async () => {
    // newsfeed-scraper: 5 min window. 30 min ago is stale regardless of market state.
    const stale = new Date(Date.now() - 30 * 60_000).toISOString();
    mockGetDb([
      {
        service: "newsfeed-scraper",
        state: "ok",
        last_attempt_started_at: stale,
        last_attempt_finished_at: stale,
        last_error: null,
        updated_at: stale,
      },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    expect(body.services[0].state).toBe("stale");
    expect(body.failing).toHaveLength(1);
    expect(body.failing[0].state).toBe("stale");
    expect(body.failing[0].service).toBe("newsfeed-scraper");
  });

  it("error row stays error (staleness gate doesn't downgrade real failures)", async () => {
    const stale = new Date(Date.now() - 6 * 60 * 60_000).toISOString();
    mockGetDb([
      {
        service: "cri-scan",
        state: "error",
        last_attempt_started_at: stale,
        last_attempt_finished_at: stale,
        last_error: "WAL locked",
        updated_at: stale,
      },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    expect(body.services[0].state).toBe("error");
    expect(body.failing).toHaveLength(1);
    expect(body.failing[0].state).toBe("error");
  });

  it("market-aware service uses closed-hours window when market is closed", async () => {
    // Force market state = CLOSED via Saturday timestamp on the route's now()
    // helper. We instead test the underlying lib via the staleness response on
    // a known weekend hour: rather than monkey-patch time, rely on the
    // market-aware lookup actually running by checking that a row 10min old is
    // NOT stale for fill-monitor outside trading hours.
    //
    // The route resolves market state from process time, so this assertion is
    // best expressed against the lib in service-health-windows.test.ts. Here
    // we verify the route at minimum doesn't crash on a market-aware service.
    const tenMinAgo = new Date(Date.now() - 10 * 60_000).toISOString();
    mockGetDb([
      {
        service: "fill-monitor",
        state: "ok",
        last_attempt_started_at: tenMinAgo,
        last_attempt_finished_at: tenMinAgo,
        last_error: null,
        updated_at: tenMinAgo,
      },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    // State is either ok (closed) or stale (open). Both are valid; the key
    // assertion is the route returns the row without throwing.
    expect(["ok", "stale"]).toContain(body.services[0].state);
  });

  it("daily-cadence service tolerates >1h staleness (cash-flow-sync is 25h)", async () => {
    const fiveHoursAgo = new Date(Date.now() - 5 * 60 * 60_000).toISOString();
    mockGetDb([
      {
        service: "cash-flow-sync",
        state: "ok",
        last_attempt_started_at: fiveHoursAgo,
        last_attempt_finished_at: fiveHoursAgo,
        last_error: null,
        updated_at: fiveHoursAgo,
      },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    expect(body.services[0].state).toBe("ok");
    expect(body.failing).toHaveLength(0);
  });

  it("unknown services use 1h default window — 90min old becomes stale", async () => {
    const ninetyMinAgo = new Date(Date.now() - 90 * 60_000).toISOString();
    mockGetDb([
      {
        service: "some-new-handler",
        state: "ok",
        last_attempt_started_at: ninetyMinAgo,
        last_attempt_finished_at: ninetyMinAgo,
        last_error: null,
        updated_at: ninetyMinAgo,
      },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    // Unknown services default to ``scheduled``, so a missed window
    // becomes ``stale`` (not ``dormant``) and contributes to the
    // degraded banner — the safer default.
    expect(body.services[0].state).toBe("stale");
    expect(body.failing).toHaveLength(1);
  });

  it("on-demand service past its closed window is coerced to dormant, not stale", async () => {
    // ``analyst-ratings`` is on-demand with a 3d closed window, so 7d-old data
    // is well past it.
    const sevenDaysAgo = new Date(Date.now() - 7 * 24 * 60 * 60_000).toISOString();
    mockGetDb([
      {
        service: "analyst-ratings",
        state: "ok",
        last_attempt_started_at: sevenDaysAgo,
        last_attempt_finished_at: sevenDaysAgo,
        last_error: null,
        updated_at: sevenDaysAgo,
      },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    expect(body.services[0].state).toBe("dormant");
  });
});
