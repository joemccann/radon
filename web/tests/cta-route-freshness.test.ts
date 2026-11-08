import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockSpawn = vi.fn();
const mockExecute = vi.fn();

vi.mock("child_process", () => ({
  spawn: mockSpawn,
}));

vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => ({ execute: mockExecute }) }));

function makeCtaPayload(date: string) {
  return {
    date,
    fetched_at: `${date}T21:05:00Z`,
    source: "menthorq_s3_vision",
    tables: {
      main: [
        {
          underlying: "E-Mini S&P 500 Index",
          position_today: 0.45,
          position_yesterday: 0.42,
          position_1m_ago: 0.61,
          percentile_1m: 14,
          percentile_3m: 18,
          percentile_1y: 25,
          z_score_3m: -1.44,
        },
      ],
      index: [],
      commodity: [],
      currency: [],
    },
  };
}

type HealthRow = {
  state: string;
  last_attempt_started_at: string | null;
  last_attempt_finished_at: string | null;
  last_error: string | null;
  updated_at: string;
};

function mockTurso({ cta, ageSeconds = 0, health }: {
  cta?: ReturnType<typeof makeCtaPayload>;
  ageSeconds?: number;
  health?: HealthRow;
}) {
  mockExecute.mockImplementation(async ({ sql }: { sql: string }) => {
    if (sql.includes("menthorq_cta")) {
      return {
        rows: cta
          ? [{
              date: cta.date,
              payload: JSON.stringify(cta),
              fetched_at: new Date(Date.now() - ageSeconds * 1000).toISOString(),
            }]
          : [],
      };
    }
    if (sql.includes("service_health")) return { rows: health ? [health] : [] };
    throw new Error(`unexpected query: ${sql}`);
  });
}

describe("GET /api/menthorq/cta — freshness contract", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-11T22:15:00Z"));
    vi.spyOn(console, "warn").mockImplementation(() => {});

    mockSpawn.mockReturnValue({
      stdout: { on: vi.fn() },
      stderr: { on: vi.fn() },
      on: vi.fn(),
      unref: vi.fn(),
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("marks the cache stale when the newest row is behind the latest closed trading day and triggers a background sync", async () => {
    mockTurso({ cta: makeCtaPayload("2026-03-10"), ageSeconds: 3_600 });

    const { GET } = await import("../app/api/menthorq/cta/route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.date).toBe("2026-03-10");
    expect(body.cache_meta).toMatchObject({
      is_stale: true,
      target_date: "2026-03-11",
      latest_cache_date: "2026-03-10",
      stale_reason: "behind_target",
    });
    expect(body.cache_meta.age_seconds).toBeGreaterThanOrEqual(3_595);
    expect(body.sync_status).toBeNull();

    expect(mockSpawn).toHaveBeenCalledOnce();
    const [cmd, args] = mockSpawn.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("bash");
    expect(args.some((arg) => arg.includes("scripts/run_cta_sync.sh"))).toBe(true);
  });

  it("returns a fresh cache_meta contract and does not trigger sync when the latest row matches the target trading day", async () => {
    mockTurso({
      cta: makeCtaPayload("2026-03-11"),
      ageSeconds: 120,
      health: {
        state: "ok",
        last_attempt_started_at: "2026-03-11T22:05:00Z",
        last_attempt_finished_at: "2026-03-11T22:05:31Z",
        last_error: null,
        updated_at: "2026-03-11T22:05:31Z",
      },
    });

    const { GET } = await import("../app/api/menthorq/cta/route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.cache_meta).toMatchObject({
      is_stale: false,
      target_date: "2026-03-11",
      latest_cache_date: "2026-03-11",
      stale_reason: "fresh",
    });
    expect(body.sync_status).toMatchObject({
      status: "success",
      target_date: "2026-03-11",
    });
    expect(mockSpawn).not.toHaveBeenCalled();
  });

  it("uses the computed trading day and maps a failed service_health row to degraded", async () => {
    // service_health carries no target_date: the route must use the computed date.
    mockTurso({
      cta: makeCtaPayload("2026-03-10"),
      ageSeconds: 86_400,
      health: {
        state: "error",
        last_attempt_started_at: "2026-03-11T02:55:58Z",
        last_attempt_finished_at: "2026-03-11T02:56:58Z",
        last_error: JSON.stringify({ type: "unexpected_failure", message: "S3 download failed" }),
        updated_at: "2026-03-11T02:56:58Z",
      },
    });

    const { GET } = await import("../app/api/menthorq/cta/route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.cache_meta.target_date).toBe("2026-03-11");
    expect(body.cache_meta.is_stale).toBe(true);
    expect(body.cache_meta.stale_reason).toBe("behind_target");
    expect(body.sync_health).toMatchObject({
      state: "degraded",
      target_date: "2026-03-11",
      last_error: { type: "unexpected_failure", message: "S3 download failed" },
    });
    expect(body.sync_status).toMatchObject({
      status: "error",
      error_type: "unexpected_failure",
      error_excerpt: "S3 download failed",
    });
    expect(mockSpawn).toHaveBeenCalledOnce();
    const [, args] = mockSpawn.mock.calls[0] as [string, string[]];
    expect(args).toContain("2026-03-11");
  });

  it("does not retrigger while service_health says a recent sync is running", async () => {
    mockTurso({
      cta: makeCtaPayload("2026-03-10"),
      health: {
        state: "syncing",
        last_attempt_started_at: new Date(Date.now() - 60_000).toISOString(),
        last_attempt_finished_at: null,
        last_error: null,
        updated_at: new Date(Date.now() - 60_000).toISOString(),
      },
    });

    const { GET } = await import("../app/api/menthorq/cta/route");
    await GET();
    expect(mockSpawn).not.toHaveBeenCalled();
  });

  it("serves the last good row from memory when Turso fails, still judged against the target date", async () => {
    mockTurso({ cta: makeCtaPayload("2026-03-10"), ageSeconds: 3_600 });
    const { GET } = await import("../app/api/menthorq/cta/route");
    await GET();
    mockExecute.mockRejectedValue(new Error("offline"));

    const response = await GET();
    const body = await response.json();
    expect(response.status).toBe(200);
    expect(body.date).toBe("2026-03-10");
    expect(body.cache_meta.is_stale).toBe(true);
    expect(body.cache_meta.stale_reason).toBe("behind_target");
  });

  it("returns a degraded 503 response with stale metadata when no CTA row exists yet", async () => {
    mockTurso({});

    const { GET } = await import("../app/api/menthorq/cta/route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(503);
    expect(body.date).toBeNull();
    expect(body.tables).toBeNull();
    expect(body.cache_meta).toMatchObject({
      is_stale: true,
      target_date: "2026-03-11",
      latest_cache_date: null,
      stale_reason: "missing_cache",
    });
    expect(mockSpawn).toHaveBeenCalledOnce();
  });

  it("treats a row with no tables as absent", async () => {
    mockExecute.mockImplementation(async ({ sql }: { sql: string }) => ({
      rows: sql.includes("menthorq_cta")
        ? [{ date: "2026-03-11", payload: JSON.stringify({ date: "2026-03-11" }), fetched_at: "2026-03-11T21:00:00Z" }]
        : [],
    }));
    const { GET } = await import("../app/api/menthorq/cta/route");
    expect((await GET()).status).toBe(503);
  });
});
