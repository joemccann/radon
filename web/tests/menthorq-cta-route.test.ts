import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockExecute = vi.fn();
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => ({ execute: mockExecute }) }));

vi.mock("child_process", () => ({
  spawn: vi.fn(() => ({
    stdout: { on: vi.fn() },
    stderr: { on: vi.fn() },
    on: vi.fn(),
    unref: vi.fn(),
  })),
}));

import { spawn } from "child_process";

async function importRoute() {
  return import(`../app/api/menthorq/cta/route?t=${Date.now()}`);
}

function mockTurso(date: string, health?: { state: string; last_error?: unknown }) {
  mockExecute.mockImplementation(async ({ sql }: { sql: string }) => {
    if (sql.includes("menthorq_cta")) {
      return {
        rows: [{
          date,
          fetched_at: new Date(Date.now() - 60_000).toISOString(),
          payload: JSON.stringify({
            date,
            fetched_at: `${date}T21:30:00Z`,
            tables: { main: [{ underlying: "SPX" }], index: [], commodity: [], currency: [] },
          }),
        }],
      };
    }
    if (sql.includes("service_health") && health) {
      return {
        rows: [{
          state: health.state,
          last_attempt_started_at: "2026-03-12T21:35:00Z",
          last_attempt_finished_at: "2026-03-12T21:35:31Z",
          last_error: health.last_error ? JSON.stringify(health.last_error) : null,
          updated_at: "2026-03-12T21:35:31Z",
        }],
      };
    }
    return { rows: [] };
  });
}

describe("GET /api/menthorq/cta", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it("returns stale cache metadata and triggers background sync when expected date is newer than latest row", async () => {
    vi.setSystemTime(new Date("2026-03-12T22:15:00Z"));
    mockTurso("2026-03-11", {
      state: "error",
      last_error: { type: "auth_rejected", message: "Your username or password was incorrect" },
    });

    const { GET } = await importRoute();
    const response = await GET();
    const body = await response.json();

    expect(body.cache_meta.is_stale).toBe(true);
    expect(body.cache_meta.target_date).toBe("2026-03-12");
    expect(body.cache_meta.latest_cache_date).toBe("2026-03-11");
    expect(body.cache_meta.stale_reason).toBe("behind_target");
    expect(body.sync_status.status).toBe("error");
    expect(body.sync_status.error_type).toBe("auth_rejected");
    expect(vi.mocked(spawn)).toHaveBeenCalledTimes(1);
  });

  it("returns fresh cache metadata and does not trigger sync when latest row matches target date", async () => {
    vi.setSystemTime(new Date("2026-03-12T22:15:00Z"));
    mockTurso("2026-03-12", { state: "ok" });

    const { GET } = await importRoute();
    const response = await GET();
    const body = await response.json();

    expect(body.cache_meta.is_stale).toBe(false);
    expect(body.cache_meta.target_date).toBe("2026-03-12");
    expect(body.cache_meta.latest_cache_date).toBe("2026-03-12");
    expect(body.cache_meta.stale_reason).toBe("fresh");
    expect(body.sync_status.status).toBe("success");
    expect(vi.mocked(spawn)).not.toHaveBeenCalled();
  });

  it("holiday after close uses the previous trading session", async () => {
    vi.setSystemTime(new Date("2026-07-03T22:15:00Z"));
    mockTurso("2026-07-02");

    const { GET } = await importRoute();
    const body = await (await GET()).json();
    expect(body.cache_meta.target_date).toBe("2026-07-02");
    expect(body.cache_meta.is_stale).toBe(false);
    expect(vi.mocked(spawn)).not.toHaveBeenCalled();
  });
});
