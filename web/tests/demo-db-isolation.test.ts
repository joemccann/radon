/**
 * @vitest-environment node
 *
 * REL-245 (R-653): demo principal implies demo DB, asserted in-code.
 * A demo deploy that carries the PROD `TURSO_DB_URL` must never serve
 * DB rows to a demo principal — orders fall back to fixture-only and
 * portfolio refuses outright, independent of the (skipped) CI guard
 * `scripts/ci/check_demo_isolation.py`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  radonFetch: vi.fn(),
  readOrders: vi.fn(),
  readPortfolioSnapshot: vi.fn(),
  requireRouteAccess: vi.fn(),
}));

vi.mock("@/lib/routeAccess", () => ({
  requireRouteAccess: mocks.requireRouteAccess,
}));

vi.mock("@/lib/radonApi", () => ({
  radonFetch: mocks.radonFetch,
}));

vi.mock("@/lib/orders/readOrdersFromDb", () => ({
  readOrdersSnapshotFromDb: mocks.readOrders,
}));

vi.mock("@/lib/portfolio/readPortfolioSnapshot.server", () => ({
  readPortfolioSnapshot: mocks.readPortfolioSnapshot,
  readPortfolioFromDb: vi.fn(),
  withoutPortfolioEntryDates: (d: unknown) => d,
  PortfolioSnapshotCorruptError: class PortfolioSnapshotCorruptError extends Error {},
}));

vi.mock("@/lib/dbExecute", () => ({
  dbExecute: vi.fn(async () => ({ rows: [] })),
}));

const PROD_URL = "libsql://radon-joemccann.aws-us-west-2.turso.io";
const DEMO_URL = "libsql://radon-demo-joemccann.aws-us-west-2.turso.io";

const ORIGINAL_TURSO_URL = process.env.TURSO_DB_URL;

const DB_SNAPSHOT = {
  last_sync: "2026-09-05T12:00:00Z",
  open_orders: [{ orderId: 99, symbol: "TSLA", status: "Submitted" }],
  executed_orders: [],
  open_count: 1,
  executed_count: 0,
};

function setPrincipal(kind: "demo" | "operator"): void {
  mocks.requireRouteAccess.mockResolvedValue({
    ok: true,
    principal: { userId: kind === "demo" ? "demo-user" : "operator", kind },
  });
}

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  mocks.readOrders.mockResolvedValue(DB_SNAPSHOT);
  mocks.readPortfolioSnapshot.mockResolvedValue({
    snapshot: { data: { positions: [{ ticker: "REAL" }], bankroll: 123456 }, syncedAtMs: Date.now() },
    warning: null,
  });
});

afterEach(() => {
  if (ORIGINAL_TURSO_URL === undefined) delete process.env.TURSO_DB_URL;
  else process.env.TURSO_DB_URL = ORIGINAL_TURSO_URL;
});

describe("demoDbIsolationViolation", () => {
  it("flags a prod-marked TURSO_DB_URL and passes a demo one", async () => {
    const { demoDbIsolationViolation } = await import("@/lib/demo/demoDbIsolation");
    expect(demoDbIsolationViolation({ TURSO_DB_URL: PROD_URL })).toMatch(/radon-joemccann/);
    expect(demoDbIsolationViolation({ TURSO_DB_URL: DEMO_URL })).toBeNull();
    expect(demoDbIsolationViolation({})).toBeNull();
  });
});

describe("GET /api/orders under a demo principal", () => {
  it("serves fixtures only and never reads the DB when TURSO_DB_URL is the prod DB", async () => {
    process.env.TURSO_DB_URL = PROD_URL;
    setPrincipal("demo");
    const { GET } = await import("@/app/api/orders/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(mocks.readOrders).not.toHaveBeenCalled();
    expect(body.open_orders).toEqual([]);
    expect(body.open_count).toBe(0);
    expect(body.executed_orders.every((o: { execId: string }) => o.execId.startsWith("DEMO-"))).toBe(true);
  });

  it("still merges demo-DB open orders when TURSO_DB_URL is a demo DB", async () => {
    process.env.TURSO_DB_URL = DEMO_URL;
    setPrincipal("demo");
    const { GET } = await import("@/app/api/orders/route");
    const res = await GET();
    const body = await res.json();
    expect(mocks.readOrders).toHaveBeenCalledTimes(1);
    expect(body.open_orders).toEqual(DB_SNAPSHOT.open_orders);
  });
});

describe("GET /api/portfolio under a demo principal", () => {
  it("refuses without reading the DB when TURSO_DB_URL is the prod DB", async () => {
    process.env.TURSO_DB_URL = PROD_URL;
    setPrincipal("demo");
    const { GET } = await import("@/app/api/portfolio/route");
    const res = await GET();
    expect(res.status).toBe(403);
    expect(mocks.readPortfolioSnapshot).not.toHaveBeenCalled();
    const body = await res.json();
    expect(JSON.stringify(body)).not.toContain("REAL");
    expect(JSON.stringify(body)).not.toContain("123456");
  });

  it("serves the demo DB snapshot when TURSO_DB_URL is a demo DB", async () => {
    process.env.TURSO_DB_URL = DEMO_URL;
    setPrincipal("demo");
    const { GET } = await import("@/app/api/portfolio/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(mocks.readPortfolioSnapshot).toHaveBeenCalledTimes(1);
  });
});

describe("operator path unchanged", () => {
  it("serves the prod portfolio to the operator even with the prod TURSO_DB_URL", async () => {
    process.env.TURSO_DB_URL = PROD_URL;
    setPrincipal("operator");
    const { GET } = await import("@/app/api/portfolio/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.bankroll).toBe(123456);
  });

  it("serves DB orders to the operator even with the prod TURSO_DB_URL", async () => {
    process.env.TURSO_DB_URL = PROD_URL;
    setPrincipal("operator");
    const { GET } = await import("@/app/api/orders/route");
    const res = await GET();
    const body = await res.json();
    expect(body.open_orders).toEqual(DB_SNAPSHOT.open_orders);
  });
});
