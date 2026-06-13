/**
 * DUR-16: /api/probe/freshness route wiring — Turso reads + pinned clock.
 *
 * The route trusts the middleware bearer gate for auth (the perimeter); its
 * own job is honest data: per-source queries individually guarded so a
 * missing table or dead DB degrades to "can't prove fresh" during RTH
 * (fresh=false) and "expected quiet" off-hours — always 200, never 4xx/5xx
 * for legitimately-quiet states. Cache contract: no-store headers.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockGetDb = vi.fn();
vi.mock("@/lib/db", () => ({ getDb: mockGetDb }));

// Wednesday 2026-06-10 11:00 ET — RTH.
const OPEN_NOW = Date.parse("2026-06-10T15:00:00Z");
// Sunday — closed.
const CLOSED_NOW = Date.parse("2026-06-07T15:00:00Z");

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

type RowMap = {
  relay?: Record<string, unknown> | null;
  vcg?: string | null;
  gex?: string | null;
  journal?: string | null;
};

function dbStub({ relay, vcg, gex, journal }: RowMap) {
  return {
    execute: vi.fn(async ({ sql }: { sql: string }) => {
      if (sql.includes("service_health")) return { rows: relay ? [relay] : [] };
      if (sql.includes("vcg_snapshots")) return { rows: [{ scan_time: vcg ?? null }] };
      if (sql.includes("gex_snapshots")) return { rows: [{ scan_time: gex ?? null }] };
      if (sql.includes("journal")) return { rows: [{ written_at: journal ?? null }] };
      throw new Error(`unexpected sql: ${sql}`);
    }),
  };
}

const FRESH_DB: RowMap = {
  relay: {
    state: "ok",
    last_error: JSON.stringify({
      heartbeat: "tick",
      last_tick_at: iso(OPEN_NOW - 10_000),
      tick_age_secs: 10,
      active_subscriptions: 12,
    }),
    updated_at: iso(OPEN_NOW - 30_000),
  },
  vcg: iso(OPEN_NOW - 5 * 60_000),
  gex: iso(OPEN_NOW - 5 * 60_000),
  journal: iso(OPEN_NOW - 3_600_000),
};

async function getRoute() {
  const { GET } = await import("../app/api/probe/freshness/route");
  return GET;
}

describe("/api/probe/freshness", () => {
  beforeEach(() => {
    vi.resetModules();
    mockGetDb.mockReset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("RTH all-fresh: 200, contract shape, all_fresh=true, no-store", async () => {
    vi.setSystemTime(OPEN_NOW);
    mockGetDb.mockReturnValue(dbStub(FRESH_DB));

    const res = await (await getRoute())();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(res.headers.get("Cache-Control")).toContain("no-store");
    expect(body.market_state).toBe("open");
    expect(body.generated_at).toBe(iso(OPEN_NOW));
    expect(body.checks.relay_tick).toEqual({ applicable: true, age_secs: 10, fresh: true });
    expect(body.checks.vcg_scan).toEqual({ applicable: true, age_secs: 300, fresh: true });
    expect(body.checks.gex_scan).toEqual({ applicable: false, age_secs: null, fresh: null });
    expect(body.checks.journal).toEqual({ applicable: true, age_secs: 3600, fresh: true });
    expect(body.all_fresh).toBe(true);
  });

  it("RTH with a stale vcg scan: all_fresh=false, still 200", async () => {
    vi.setSystemTime(OPEN_NOW);
    mockGetDb.mockReturnValue(dbStub({ ...FRESH_DB, vcg: iso(OPEN_NOW - 60 * 60_000) }));

    const res = await (await getRoute())();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.checks.vcg_scan.fresh).toBe(false);
    expect(body.all_fresh).toBe(false);
  });

  it("closed market: every check inapplicable, all_fresh=null", async () => {
    vi.setSystemTime(CLOSED_NOW);
    mockGetDb.mockReturnValue(dbStub(FRESH_DB));

    const res = await (await getRoute())();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.market_state).toBe("closed");
    for (const check of Object.values(body.checks) as Array<Record<string, unknown>>) {
      expect(check).toEqual({ applicable: false, age_secs: null, fresh: null });
    }
    expect(body.all_fresh).toBeNull();
  });

  it("a single failing query degrades only its check, not the response", async () => {
    vi.setSystemTime(OPEN_NOW);
    const stub = dbStub(FRESH_DB);
    const inner = stub.execute;
    stub.execute = vi.fn(async (arg: { sql: string }) => {
      if (arg.sql.includes("vcg_snapshots")) throw new Error("no such table: vcg_snapshots");
      return inner(arg);
    });
    mockGetDb.mockReturnValue(stub);

    const res = await (await getRoute())();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.checks.vcg_scan).toEqual({ applicable: true, age_secs: null, fresh: false });
    expect(body.checks.journal.fresh).toBe(true);
    expect(body.all_fresh).toBe(false);
  });

  it("a dead DB during RTH still returns 200 — checks unproven, all_fresh=false", async () => {
    vi.setSystemTime(OPEN_NOW);
    mockGetDb.mockImplementation(() => {
      throw new Error("getDb: TURSO_DB_URL is not set");
    });

    const res = await (await getRoute())();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.checks.relay_tick).toEqual({ applicable: true, age_secs: null, fresh: false });
    expect(body.all_fresh).toBe(false);
  });
});
