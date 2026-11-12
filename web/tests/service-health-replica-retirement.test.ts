/**
 * @vitest-environment node
 *
 * Retired writers must not leave their historical service_health rows looking
 * like a live outage. The replica-watchdog handler was deleted with the
 * embedded replica, so its rows are hidden whatever is on disk.
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

describe("retired replica-watchdog", () => {
  const stale = () => new Date(Date.now() - 48 * 60 * 60_000).toISOString();

  it("has no freshness window: the handler is deleted", async () => {
    const { SERVICE_FRESHNESS_WINDOWS } = await import("../lib/serviceHealthWindows");
    expect(SERVICE_FRESHNESS_WINDOWS["replica-watchdog"]).toBeUndefined();
  });

  it("omits the historical row from public health even if a replica file exists", async () => {
    mockReplicaExists(true);
    const fresh = new Date().toISOString();
    mockDbResults([healthRow("replica-watchdog", stale()), healthRow("newsfeed-scraper", fresh)]);

    const { GET } = await import("../app/api/service-health/route");
    const body = await (await GET()).json();

    expect(body.services.map((row: DbRow) => row.service)).toEqual(["newsfeed-scraper"]);
    expect(body.failing).toEqual([]);
  });

  it("omits the historical row from edge health even if a replica file exists", async () => {
    mockReplicaExists(true);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      service_health: { state: "ok", row_count: 1, rows: [healthRow("replica-watchdog", stale())] },
    }), { status: 200 })));

    const { GET } = await import("../app/api/admin/edge-health/route");
    const body = await (await GET()).json();

    expect(body.service_health.rows).toEqual([]);
    expect(body.service_health.row_count).toBe(0);
  });

  it("omits its reliability events and baseline even if a replica file exists", async () => {
    mockReplicaExists(true);
    const inWindow = new Date(Date.now() - 60 * 60_000).toISOString();
    mockDbResults(
      [{ id: 1, service: "replica-watchdog", state: "error", detail: "failure", created_at: inWindow }],
      [{ service: "replica-watchdog", state: "ok", created_at: inWindow }],
    );

    const { GET } = await import("../app/api/admin/reliability/route");
    const body = await (await GET()).json();

    expect(body.events).toEqual([]);
    expect(body.baseline).toEqual({});
  });
});

describe("retired dropbox-research review health", () => {
  it("omits the stale row from failing health and points review at the live services", async () => {
    mockReplicaExists(false);
    const stale = "2026-10-15T20:02:02Z";
    const live = new Date().toISOString();
    mockDbResults([
      {
        ...healthRow("dropbox-research", stale, "error"),
        last_error: JSON.stringify({
          stages: [{ stage: "review", state: "error", error: "DbHttpError" }],
        }),
      },
      healthRow("marketdesk-research", live, "ok"),
      { ...healthRow("marketdesk-pdf", live, "error"), last_error: "preview_quota" },
    ]);
    const { GET } = await import("../app/api/service-health/route");
    const body = await (await GET()).json();
    expect(body.services.map((row: DbRow) => row.service)).toEqual([
      "marketdesk-research",
      "marketdesk-pdf",
    ]);
    expect(body.failing.map((row: DbRow) => row.service)).toEqual(["marketdesk-pdf"]);
    expect(JSON.stringify(body)).not.toContain("DbHttpError");
    expect(body.research.review_stage).toMatchObject({
      retired: true,
      retired_service: "dropbox-research",
      retired_unit: "radon-research.service",
      runs_on: [],
    });
    expect(body.research.review_stage.live_services).toEqual([
      { service: "marketdesk-research", state: "ok", updated_at: live },
      { service: "marketdesk-pdf", state: "error", updated_at: live },
    ]);
  });
});


describe("retired exit-orders health applicability", () => {
  it("omits the unscheduled legacy control while preserving unknown writers", async () => {
    mockReplicaExists(false);
    const stale = "2020-01-01T00:00:00Z";
    mockDbResults([healthRow("exit-orders", stale), healthRow("unknown-writer", stale)]);
    const { GET } = await import("../app/api/service-health/route");
    const body = await (await GET()).json();
    expect(body.services.map((row: DbRow) => row.service)).toEqual(["unknown-writer"]);
    expect(body.degraded_count).toBe(1);
  });

  it("filters current rows and historical baselines without deleting stored records", async () => {
    const { filterApplicableServiceHealthRows, filterApplicableServiceHealthBaseline } = await import("../lib/serviceHealthApplicability");
    const rows = [{ service: "exit-orders", state: "ok" }, { service: "fill-monitor", state: "error" }];
    expect(filterApplicableServiceHealthRows(rows)).toEqual([rows[1]]);
    expect(rows).toHaveLength(2);
    expect(filterApplicableServiceHealthBaseline({ "exit-orders": "ok", "fill-monitor": "error" }))
      .toEqual({ "fill-monitor": "error" });
  });
});
