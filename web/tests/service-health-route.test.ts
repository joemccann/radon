/**
 * @vitest-environment node
 *
 * Tests for /api/service-health — DB-only route added in Phase 0.
 * Validates failing-row classification + graceful DB-down behaviour.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

type MockRow = Record<string, unknown>;

function mockGetDb(rows: MockRow[]): void {
  vi.doMock("@/lib/db", () => ({
    getDb: () => ({
      execute: vi.fn().mockResolvedValue({ rows }),
    }),
    resetDb: () => {},
  }));
}

function mockDbThrows(message: string): void {
  vi.doMock("@/lib/db", () => ({
    getDb: () => ({
      execute: vi.fn().mockRejectedValue(new Error(message)),
    }),
    resetDb: () => {},
  }));
}

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllEnvs();
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe("/api/service-health", () => {
  it("returns empty list with summary when no rows exist", async () => {
    mockGetDb([]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    expect(body.services).toEqual([]);
    expect(body.failing).toEqual([]);
    expect(body.summary).toEqual({ total: 0, failing_count: 0 });
  });

  it("classifies state=ok rows as not failing", async () => {
    const recent = new Date().toISOString();
    mockGetDb([
      {
        service: "portfolio-sync",
        state: "ok",
        last_attempt_started_at: recent,
        last_attempt_finished_at: recent,
        last_error: null,
        updated_at: recent,
      },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    expect(body.services).toHaveLength(1);
    expect(body.failing).toHaveLength(0);
  });

  it("classifies state=error as failing", async () => {
    const now = new Date().toISOString();
    mockGetDb([
      {
        service: "cri-scan",
        state: "error",
        last_attempt_started_at: now,
        last_attempt_finished_at: now,
        last_error: "WAL locked",
        updated_at: now,
      },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    expect(body.failing).toHaveLength(1);
    expect(body.failing[0].service).toBe("cri-scan");
    expect(body.failing[0].last_error).toBe("WAL locked");
  });

  it("does NOT classify within-window OK rows as failing — daily-cadence services tolerate hours of silence", async () => {
    // cash-flow-sync has a 25h window regardless of market state, so 6h
    // ago is unambiguously fresh.
    const recent = new Date(Date.now() - 6 * 60 * 60 * 1000).toISOString();
    mockGetDb([
      {
        service: "cash-flow-sync",
        state: "ok",
        last_attempt_started_at: recent,
        last_attempt_finished_at: recent,
        last_error: null,
        updated_at: recent,
      },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    expect(body.failing).toHaveLength(0);
  });

  it("does NOT classify state=syncing or state=paused as failing", async () => {
    const now = new Date().toISOString();
    mockGetDb([
      { service: "a", state: "syncing", last_attempt_started_at: now, last_attempt_finished_at: null, last_error: null, updated_at: now },
      { service: "b", state: "paused", last_attempt_started_at: now, last_attempt_finished_at: now, last_error: null, updated_at: now },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    expect(body.failing).toHaveLength(0);
  });

  it("returns a degraded provider row when DB is unreachable", async () => {
    mockDbThrows("connection refused");
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    expect(res.status).toBe(200); // graceful, not 500
    const body = await res.json();
    expect(body.services).toHaveLength(1);
    expect(body.services[0]).toMatchObject({
      service: "turso-db",
      state: "error",
      category: "scheduled",
    });
    expect(body.failing).toHaveLength(1);
    expect(body.degraded_count).toBe(1);
    expect(body.summary).toEqual({ total: 1, failing_count: 1 });
    expect(body.warning).toBe("Service health store unavailable");
    expect(JSON.stringify(body)).not.toContain("connection refused");
  });

  it("sets cache: no-store / force-dynamic headers", async () => {
    mockGetDb([]);
    const { GET, dynamic } = await import("../app/api/service-health/route");
    expect(dynamic).toBe("force-dynamic");
    const res = await GET();
    expect(res.headers.get("cache-control") || "").toMatch(/no-store/);
  });

  it("normalises JSON-encoded last_error into a plain error_summary", async () => {
    const now = new Date().toISOString();
    mockGetDb([
      {
        service: "cash-flow-sync",
        state: "error",
        last_attempt_started_at: now,
        last_attempt_finished_at: now,
        last_error: JSON.stringify({
          message: "ERR: cash flow fetch failed: Flex SendRequest failed (code 1001)",
        }),
        updated_at: now,
      },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    expect(body.failing).toHaveLength(1);
    const row = body.failing[0];
    // Raw payload preserved on the wire for diagnostic clients.
    expect(row.last_error).toContain("{");
    // But the summary used by the banner is plain prose.
    expect(typeof row.error_summary).toBe("string");
    expect(row.error_summary).toContain("ERR: cash flow fetch failed");
    expect(row.error_summary).not.toContain("{");
    expect(row.error_summary).not.toContain('"');
  });

  it("emits null error_summary when last_error itself is null", async () => {
    const now = new Date().toISOString();
    mockGetDb([
      {
        service: "scanner",
        state: "error",
        last_attempt_started_at: now,
        last_attempt_finished_at: now,
        last_error: null,
        updated_at: now,
      },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    expect(body.failing[0].error_summary).toBeNull();
  });
});

/**
 * Category-aware response: scheduled services that miss their window
 * become ``stale`` and contribute to ``degraded_count`` (banner red).
 * On-demand services that miss their window become ``dormant`` and
 * contribute to ``dormant_count`` (banner informational chip).
 *
 * Both shapes ride the same /api/service-health response so the banner
 * can read one number for each treatment.
 */
describe("/api/service-health — category-aware response shape", () => {
  it("includes `category` on every row so clients can group locally", async () => {
    const now = new Date().toISOString();
    mockGetDb([
      {
        service: "newsfeed-scraper",
        state: "ok",
        last_attempt_started_at: now,
        last_attempt_finished_at: now,
        last_error: null,
        updated_at: now,
      },
      {
        service: "scanner",
        state: "ok",
        last_attempt_started_at: now,
        last_attempt_finished_at: now,
        last_error: null,
        updated_at: now,
      },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    const byName = Object.fromEntries(
      body.services.map((row: { service: string; category: string }) => [row.service, row.category]),
    );
    expect(byName["newsfeed-scraper"]).toBe("scheduled");
    expect(byName["scanner"]).toBe("scheduled");
  });

  it("coerces past-window scheduled rows to state=stale and counts them in degraded_count", async () => {
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
    expect(body.degraded_count).toBe(1);
    expect(body.dormant_count).toBe(0);
  });

  it("coerces past-window on-demand rows to state=dormant and counts them in dormant_count", async () => {
    // analyst-ratings is on-demand; closed-window is 3d.
    const dormant = new Date(Date.now() - 7 * 24 * 60 * 60_000).toISOString();
    mockGetDb([
      {
        service: "analyst-ratings",
        state: "ok",
        last_attempt_started_at: dormant,
        last_attempt_finished_at: dormant,
        last_error: null,
        updated_at: dormant,
      },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    expect(body.services[0].state).toBe("dormant");
    expect(body.dormant_count).toBe(1);
    expect(body.degraded_count).toBe(0);
  });

  it("preserves error state regardless of category — errors always count toward degraded_count", async () => {
    const now = new Date().toISOString();
    mockGetDb([
      {
        service: "analyst-ratings",
        state: "error",
        last_attempt_started_at: now,
        last_attempt_finished_at: now,
        last_error: "boom",
        updated_at: now,
      },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    const body = await res.json();
    expect(body.services[0].state).toBe("error");
    expect(body.degraded_count).toBe(1);
    expect(body.dormant_count).toBe(0);
  });

  it("fresh ok rows contribute to neither degraded_count nor dormant_count", async () => {
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
      {
        service: "scanner",
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
    expect(body.degraded_count).toBe(0);
    expect(body.dormant_count).toBe(0);
  });

  it("mixed bag: one scheduled stale + two on-demand dormant + one healthy = 1/2/0", async () => {
    const now = Date.now();
    const stale = new Date(now - 30 * 60_000).toISOString();
    const dormantTs = new Date(now - 7 * 24 * 60 * 60_000).toISOString();
    const fresh = new Date(now).toISOString();
    mockGetDb([
      {
        service: "newsfeed-scraper",
        state: "ok",
        last_attempt_started_at: stale,
        last_attempt_finished_at: stale,
        last_error: null,
        updated_at: stale,
      },
      {
        service: "analyst-ratings",
        state: "ok",
        last_attempt_started_at: dormantTs,
        last_attempt_finished_at: dormantTs,
        last_error: null,
        updated_at: dormantTs,
      },
      {
        // Was `gex-scan`, which R-422 reclassified as SCHEDULED (the 15-minute
        // RTH driver runs it), so it would count as degraded rather than
        // dormant. `gamma-rotation-scan` is genuinely on-demand and keeps this
        // case's shape: one scheduled stale + two on-demand dormant.
        service: "gamma-rotation-scan",
        state: "ok",
        last_attempt_started_at: dormantTs,
        last_attempt_finished_at: dormantTs,
        last_error: null,
        updated_at: dormantTs,
      },
      {
        service: "journal-sync",
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
    expect(body.degraded_count).toBe(1);
    expect(body.dormant_count).toBe(2);
  });
});
