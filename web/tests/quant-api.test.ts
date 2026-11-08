/**
 * @vitest-environment node
 *
 * /api/quant/[strategy] — quant sleeve read (regime QUANT tab).
 *
 * GET-only, Turso-first through dbFirstRead: the latest quant_runs row, the
 * quant_targets rows at the latest as_of and the quant_orders rows at the
 * latest as_of for one registered strategy. Always 200 for a registered key;
 * absent data is the `QUANT_MISSING` shape, never a 4xx. An unregistered key
 * is a bad URL and gets 400, mirroring /api/backtest/[strategy].
 *
 * Mechanics mirror margin-debt-api.test.ts: @/lib/db backed by a real
 * in-memory libsql client seeded with the three tables.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createClient, type Client } from "@libsql/client";

let db: Client;
const mockGetDb = vi.fn(() => db);
vi.mock("@/lib/db", () => ({
  resetDb: () => {},
  getDb: mockGetDb,
}));

async function seedSchema(client: Client, { legacyTargets = false, withOrders = true } = {}): Promise<void> {
  await client.execute(`CREATE TABLE quant_runs (
    strategy TEXT NOT NULL, run_at TEXT NOT NULL, mode TEXT NOT NULL,
    since TEXT, until TEXT, n_sessions INTEGER NOT NULL, n_rebalances INTEGER NOT NULL,
    ann_return REAL, ann_vol REAL, sharpe REAL, max_drawdown REAL, payload TEXT NOT NULL,
    PRIMARY KEY (strategy, run_at))`);
  await client.execute(
    legacyTargets
      ? `CREATE TABLE quant_targets (
          strategy TEXT NOT NULL, as_of TEXT NOT NULL, symbol TEXT NOT NULL,
          weight REAL NOT NULL, notional REAL, recorded_at TEXT NOT NULL,
          PRIMARY KEY (strategy, as_of, symbol))`
      : `CREATE TABLE quant_targets (
          strategy TEXT NOT NULL, as_of TEXT NOT NULL, symbol TEXT NOT NULL,
          weight REAL NOT NULL, notional REAL, recorded_at TEXT NOT NULL,
          close REAL, rebalance_today INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (strategy, as_of, symbol))`,
  );
  if (withOrders) {
    await client.execute(`CREATE TABLE quant_orders (
      strategy TEXT NOT NULL, as_of TEXT NOT NULL, symbol TEXT NOT NULL,
      action TEXT NOT NULL, quantity INTEGER NOT NULL, limit_price REAL, order_ref TEXT,
      status TEXT NOT NULL, order_id INTEGER, perm_id INTEGER, message TEXT, placed_at TEXT NOT NULL,
      PRIMARY KEY (strategy, placed_at, symbol))`);
  }
}

type Payload = Record<string, unknown>;

function runPayload(overrides: Payload = {}): Payload {
  return {
    strategy: "inverse_vol",
    label: "Monthly inverse volatility",
    mode: "backtest",
    since: "2007-01-03",
    until: "2026-10-30",
    n_sessions: 3,
    n_rebalances: 1,
    cost_bps: 5,
    universe: ["SPY", "TLT"],
    dates: ["2026-10-28", "2026-10-29", "2026-10-30"],
    equity: [100000, 101000, 99000],
    returns: [0, 0.01, -0.0198],
    turnover: [1, 0, 0],
    final_weights: { SPY: 0.6, TLT: 0.4 },
    metrics: {
      sharpe: 0.67,
      sortino: 0.9,
      calmar: 0.27,
      max_drawdown: -0.204,
      hit_rate: 0.55,
      expectancy: 0.0002,
      annualized_return: 0.055,
      ann_vol: 0.082,
      total_return: 1.742,
      avg_turnover: 0.12,
      cost_drag: 0.01,
      mean_gross: 0.98,
      mean_net: 0.98,
    },
    ...overrides,
  };
}

async function insertRun(strategy: string, runAt: string, payload: Payload = runPayload()): Promise<void> {
  await db.execute({
    sql: `INSERT INTO quant_runs (strategy, run_at, mode, since, until, n_sessions, n_rebalances,
            ann_return, ann_vol, sharpe, max_drawdown, payload)
          VALUES (?, ?, 'backtest', ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    args: [
      strategy,
      runAt,
      String(payload.since),
      String(payload.until),
      Number(payload.n_sessions),
      Number(payload.n_rebalances),
      (payload.metrics as Payload).annualized_return as number,
      (payload.metrics as Payload).ann_vol as number,
      (payload.metrics as Payload).sharpe as number,
      (payload.metrics as Payload).max_drawdown as number,
      JSON.stringify(payload),
    ],
  });
}

async function insertTarget(
  strategy: string,
  asOf: string,
  symbol: string,
  weight: number,
  notional: number | null,
  opts: { close?: number | null; rebalance?: boolean; recordedAt?: string; legacy?: boolean } = {},
): Promise<void> {
  const recordedAt = opts.recordedAt ?? `${asOf}T21:00:00Z`;
  if (opts.legacy) {
    await db.execute({
      sql: `INSERT INTO quant_targets (strategy, as_of, symbol, weight, notional, recorded_at) VALUES (?, ?, ?, ?, ?, ?)`,
      args: [strategy, asOf, symbol, weight, notional, recordedAt],
    });
    return;
  }
  await db.execute({
    sql: `INSERT INTO quant_targets (strategy, as_of, symbol, weight, notional, recorded_at, close, rebalance_today)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    args: [strategy, asOf, symbol, weight, notional, recordedAt, opts.close ?? null, opts.rebalance ? 1 : 0],
  });
}

async function insertOrder(strategy: string, asOf: string, symbol: string, action: string, qty: number, status: string, placedAt: string): Promise<void> {
  await db.execute({
    sql: `INSERT INTO quant_orders (strategy, as_of, symbol, action, quantity, limit_price, order_ref, status, order_id, perm_id, message, placed_at)
          VALUES (?, ?, ?, ?, ?, 101.25, ?, ?, NULL, NULL, 'dry run', ?)`,
    args: [strategy, asOf, symbol, action, qty, `quant:${strategy}`, status, placedAt],
  });
}

async function jsonOf(res: Response): Promise<Record<string, unknown>> {
  return (await res.json()) as Record<string, unknown>;
}

async function get(strategy: string): Promise<Response> {
  const { GET } = await import("../app/api/quant/[strategy]/route");
  return GET(new Request(`http://localhost/api/quant/${strategy}`), { params: Promise.resolve({ strategy }) });
}

beforeEach(async () => {
  vi.resetModules();
  db = createClient({ url: ":memory:" });
  mockGetDb.mockReturnValue(db);
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  db.close();
  vi.restoreAllMocks();
});

describe("GET /api/quant/[strategy]", () => {
  it("serves the newest run, the latest targets and the latest orders for the strategy", async () => {
    await seedSchema(db);
    await insertRun("inverse_vol", "2026-10-01T00:00:00Z", runPayload({ n_sessions: 1 }));
    await insertRun("inverse_vol", "2026-10-30T22:00:00Z");
    await insertTarget("inverse_vol", "2026-10-29", "SPY", 0.5, 50000, { close: 500 });
    await insertTarget("inverse_vol", "2026-10-30", "SPY", 0.6, 60000, { close: 505.5, rebalance: true });
    await insertTarget("inverse_vol", "2026-10-30", "TLT", 0.4, 40000, { close: 90 });
    await insertOrder("inverse_vol", "2026-10-29", "SPY", "BUY", 5, "planned", "2026-10-29T13:35:00Z");
    await insertOrder("inverse_vol", "2026-10-30", "SPY", "BUY", 20, "planned", "2026-10-31T13:35:00Z");

    const res = await get("inverse_vol");
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.missing).toBe(false);
    expect(json.label).toBe("Monthly inverse volatility");
    const run = json.run as Payload;
    expect(run.run_at).toBe("2026-10-30T22:00:00Z");
    expect(run.n_sessions).toBe(3);
    expect((run.metrics as Payload).sharpe).toBe(0.67);
    expect(run.dates).toEqual(["2026-10-28", "2026-10-29", "2026-10-30"]);
    expect(run.equity).toEqual([100000, 101000, 99000]);
    expect(run).not.toHaveProperty("returns");
    expect(run).not.toHaveProperty("turnover");

    const targets = json.targets as Payload;
    expect(targets.as_of).toBe("2026-10-30");
    expect(targets.rebalance_today).toBe(true);
    expect(targets.rows).toEqual([
      { symbol: "SPY", weight: 0.6, notional: 60000, close: 505.5 },
      { symbol: "TLT", weight: 0.4, notional: 40000, close: 90 },
    ]);

    const orders = json.orders as Payload;
    expect(orders.as_of).toBe("2026-10-30");
    expect(orders.rows).toEqual([
      { symbol: "SPY", action: "BUY", quantity: 20, limit_price: 101.25, status: "planned", message: "dry run" },
    ]);
  });

  it("serves targets without a run, and a run without targets or orders", async () => {
    await seedSchema(db);
    await insertTarget("dual_momentum", "2026-10-30", "GLD", 0.5, null, { close: 250 });
    let json = await jsonOf(await get("dual_momentum"));
    expect(json.run).toBeNull();
    expect((json.targets as Payload).as_of).toBe("2026-10-30");
    expect(json.orders).toBeNull();

    await insertRun("monthly_rotation", "2026-10-30T22:00:00Z");
    json = await jsonOf(await get("monthly_rotation"));
    expect((json.run as Payload).run_at).toBe("2026-10-30T22:00:00Z");
    expect(json.targets).toBeNull();
    expect(json.orders).toBeNull();
  });

  it("tolerates the pre-0109 targets schema and a missing orders table", async () => {
    await seedSchema(db, { legacyTargets: true, withOrders: false });
    await insertRun("inverse_vol", "2026-10-30T22:00:00Z");
    await insertTarget("inverse_vol", "2026-10-30", "SPY", 0.6, 60000, { legacy: true });
    const json = await jsonOf(await get("inverse_vol"));
    const targets = json.targets as Payload;
    expect(targets.rebalance_today).toBe(false);
    expect(targets.rows).toEqual([{ symbol: "SPY", weight: 0.6, notional: 60000, close: null }]);
    expect(json.orders).toBeNull();
  });

  it("serves the last good read from memory when Turso loses its rows", async () => {
    await seedSchema(db);
    await insertRun("inverse_vol", "2026-10-30T22:00:00Z", runPayload({ n_sessions: 7 }));
    await get("inverse_vol");
    await db.execute("DELETE FROM quant_runs");
    const json = await jsonOf(await get("inverse_vol"));
    expect((json.run as Payload).n_sessions).toBe(7);
  });

  it("returns the contract's missing shape with HTTP 200 when nothing exists", async () => {
    await seedSchema(db);
    const res = await get("inverse_vol");
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({
      strategy: "inverse_vol",
      missing: true,
      run: null,
      targets: null,
      orders: null,
    });
  });

  it("never leaks another strategy's rows", async () => {
    await seedSchema(db);
    await insertRun("trend_inverse_vol", "2026-10-30T22:00:00Z");
    await insertTarget("trend_inverse_vol", "2026-10-30", "QQQ", -0.3, -30000, { close: 480 });
    const json = await jsonOf(await get("inverse_vol"));
    expect(json.missing).toBe(true);
  });

  it("rejects an unregistered strategy with 400, never a missing payload", async () => {
    await seedSchema(db);
    const res = await get("not_a_strategy");
    expect(res.status).toBe(400);
    expect(await jsonOf(res)).toEqual({ status: "unknown_strategy", strategy: "not_a_strategy" });
  });

  it("declares force-dynamic and the read capability", async () => {
    const route = await import("../app/api/quant/[strategy]/route");
    expect(route.dynamic).toBe("force-dynamic");
    expect(route.radonCapability).toBe("read");
  });
});
