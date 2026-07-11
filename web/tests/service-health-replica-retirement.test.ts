/**
 * @vitest-environment node
 *
 * A retired embedded replica must not leave its historical service_health row
 * looking like a live outage. The row remains applicable when the replica file
 * exists, because that installation still needs the watchdog.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

type DbRow = Record<string, unknown>;

function mockReplicaExists(exists: boolean) {
  const existsSync = vi.fn().mockReturnValue(exists);
  vi.doMock("node:fs", async () => {
    const actual = await vi.importActual<typeof import("node:fs")>("node:fs");
    return { ...actual, existsSync };
  });
  return existsSync;
}

function mockDbResults(...results: DbRow[][]): void {
  const execute = vi.fn();
  for (const rows of results) execute.mockResolvedValueOnce({ rows });
  vi.doMock("@/lib/db", () => ({
    getDb: () => ({ execute }),
    resetDb: () => {},
  }));
}

function healthRow(service: string, updatedAt: string, state = "ok"): DbRow {
  return {
    service,
    state,
    last_attempt_started_at: updatedAt,
    last_attempt_finished_at: updatedAt,
    last_error: null,
    updated_at: updatedAt,
  };
}

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.doUnmock("node:fs");
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe("replica-watchdog current health applicability", () => {
  it("omits a historical stale row from the public health response when the replica is absent", async () => {
    mockReplicaExists(false);
    const stale = new Date(Date.now() - 48 * 60 * 60_000).toISOString();
    const fresh = new Date().toISOString();
    mockDbResults([
      healthRow("replica-watchdog", stale),
      healthRow("newsfeed-scraper", fresh),
    ]);

    const { GET } = await import("../app/api/service-health/route");
    const body = await (await GET()).json();

    expect(body.services.map((row: DbRow) => row.service)).toEqual(["newsfeed-scraper"]);
    expect(body.failing).toEqual([]);
    expect(body.degraded_count).toBe(0);
    expect(body.summary).toEqual({ total: 1, failing_count: 0 });
  });

  it("keeps and stale-classifies the row when the replica exists", async () => {
    mockReplicaExists(true);
    const stale = new Date(Date.now() - 48 * 60 * 60_000).toISOString();
    mockDbResults([healthRow("replica-watchdog", stale)]);

    const { GET } = await import("../app/api/service-health/route");
    const body = await (await GET()).json();

    expect(body.services).toHaveLength(1);
    expect(body.services[0]).toMatchObject({ service: "replica-watchdog", state: "stale" });
    expect(body.failing).toHaveLength(1);
    expect(body.degraded_count).toBe(1);
  });

  it("filters the edge-health rows used by admin freshness rendering when the replica is absent", async () => {
    mockReplicaExists(false);
    const stale = new Date(Date.now() - 48 * 60 * 60_000).toISOString();
    const fresh = new Date().toISOString();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      service_health: {
        state: "ok",
        row_count: 2,
        rows: [
          healthRow("replica-watchdog", stale),
          healthRow("newsfeed-scraper", fresh),
        ],
      },
    }), { status: 200 })));

    const { GET } = await import("../app/api/admin/edge-health/route");
    const body = await (await GET()).json();

    expect(body.service_health.rows.map((row: DbRow) => row.service)).toEqual(["newsfeed-scraper"]);
    expect(body.service_health.row_count).toBe(1);
  });

  it("keeps the edge-health row used by admin freshness rendering when the replica exists", async () => {
    mockReplicaExists(true);
    const stale = new Date(Date.now() - 48 * 60 * 60_000).toISOString();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      service_health: {
        state: "ok",
        row_count: 1,
        rows: [healthRow("replica-watchdog", stale)],
      },
    }), { status: 200 })));

    const { GET } = await import("../app/api/admin/edge-health/route");
    const body = await (await GET()).json();

    expect(body.service_health.rows).toHaveLength(1);
    expect(body.service_health.rows[0].service).toBe("replica-watchdog");
    expect(body.service_health.row_count).toBe(1);
  });
});

describe("replica-watchdog reliability history applicability", () => {
  const inWindow = new Date(Date.now() - 60 * 60_000).toISOString();

  it("omits retired replica events and baseline state when the replica is absent", async () => {
    mockReplicaExists(false);
    mockDbResults(
      [
        { id: 1, service: "replica-watchdog", state: "error", detail: "old failure", created_at: inWindow },
        { id: 2, service: "newsfeed-scraper", state: "ok", detail: null, created_at: inWindow },
      ],
      [
        { service: "replica-watchdog", state: "error", created_at: inWindow },
        { service: "newsfeed-scraper", state: "ok", created_at: inWindow },
      ],
    );

    const { GET } = await import("../app/api/admin/reliability/route");
    const body = await (await GET()).json();

    expect(body.events.map((event: DbRow) => event.service)).toEqual(["newsfeed-scraper"]);
    expect(body.baseline).toEqual({ "newsfeed-scraper": "ok" });
  });

  it("keeps replica events and baseline state when the replica exists", async () => {
    mockReplicaExists(true);
    mockDbResults(
      [{ id: 1, service: "replica-watchdog", state: "error", detail: "failure", created_at: inWindow }],
      [{ service: "replica-watchdog", state: "ok", created_at: inWindow }],
    );

    const { GET } = await import("../app/api/admin/reliability/route");
    const body = await (await GET()).json();

    expect(body.events).toHaveLength(1);
    expect(body.events[0].service).toBe("replica-watchdog");
    expect(body.baseline).toEqual({ "replica-watchdog": "ok" });
  });

  it("re-evaluates replica applicability even when the DB history payload is cached", async () => {
    vi.stubEnv("RADON_DB_CACHE_FORCE", "1");
    const existsSync = mockReplicaExists(false);
    mockDbResults(
      [{ id: 1, service: "replica-watchdog", state: "error", detail: "failure", created_at: inWindow }],
      [{ service: "replica-watchdog", state: "ok", created_at: inWindow }],
    );

    const { GET } = await import("../app/api/admin/reliability/route");
    const absentBody = await (await GET()).json();
    expect(absentBody.events).toEqual([]);

    existsSync.mockReturnValue(true);
    const presentBody = await (await GET()).json();
    expect(presentBody.events).toHaveLength(1);
    expect(presentBody.events[0].service).toBe("replica-watchdog");
  });
});
