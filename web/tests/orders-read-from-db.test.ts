/**
 * @vitest-environment node
 *
 * Tests for readOrdersFromDb — Phase 3.2.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

type MockRow = Record<string, unknown>;

function mockGetDb(openRows: MockRow[], execRows: MockRow[]): { syncCalls: number } {
  const counters = { syncCalls: 0 };
  let call = 0;
  vi.doMock("@/lib/db", () => ({
    getDb: () => ({
      execute: vi.fn().mockImplementation(() => {
        call += 1;
        return Promise.resolve({ rows: call === 1 ? openRows : execRows });
      }),
    }),
    syncDb: vi.fn().mockImplementation(async () => {
      counters.syncCalls += 1;
    }),
    resetDb: vi.fn(),
  }));
  return counters;
}

beforeEach(() => {
  vi.resetModules();
});

const openPayload = (permId: number) => ({
  orderId: permId,
  permId,
  symbol: "TSLA",
  contract: { conId: 1, symbol: "TSLA", secType: "OPT", strike: 200, right: "C", expiry: "20260530" },
  action: "BUY",
  orderType: "LMT",
  totalQuantity: 1,
  limitPrice: 5,
  auxPrice: null,
  status: "PreSubmitted",
  filled: 0,
  remaining: 1,
  avgFillPrice: null,
  tif: "DAY",
});

const todayEt = new Date().toLocaleDateString("sv", { timeZone: "America/New_York" });
// Noon ET-ish ISO for "today" so day-cut keeps the row under any host TZ.
const todayFillIso = `${todayEt}T12:00:00-04:00`;

const execPayload = (execId: string, time = todayFillIso) => ({
  execId,
  symbol: "TSLA",
  contract: { conId: 1, symbol: "TSLA", secType: "OPT", strike: 200, right: "C", expiry: "20260530" },
  side: "BOT",
  quantity: 1,
  avgPrice: 5.25,
  commission: 0.65,
  realizedPNL: null,
  time,
  exchange: "ISE",
});

describe("readOrdersFromDb", () => {
  it("returns null when both tables are empty", async () => {
    mockGetDb([], []);
    const { readOrdersFromDb } = await import("../lib/orders/readOrdersFromDb");
    const result = await readOrdersFromDb();
    expect(result).toBeNull();
  });

  it("returns EMPTY_ORDERS from the snapshot helper when both tables are empty", async () => {
    mockGetDb([], []);
    const { readOrdersSnapshotFromDb } = await import("../lib/orders/readOrdersFromDb");
    const result = await readOrdersSnapshotFromDb();
    expect(result).toEqual({
      last_sync: "",
      open_orders: [],
      executed_orders: [],
      open_count: 0,
      executed_count: 0,
    });
  });

  it("returns OrdersData shape when rows exist", async () => {
    mockGetDb(
      [{ payload: JSON.stringify(openPayload(1)), updated_at: "2026-05-07T13:30:00Z" }],
      [{ payload: JSON.stringify(execPayload("e1")), fill_time: todayFillIso }],
    );
    const { readOrdersFromDb } = await import("../lib/orders/readOrdersFromDb");
    const result = await readOrdersFromDb();
    expect(result).not.toBeNull();
    expect(result!.open_count).toBe(1);
    expect(result!.executed_count).toBe(1);
    expect(result!.open_orders[0].permId).toBe(1);
    expect(result!.executed_orders[0].execId).toBe("e1");
  });

  it("excludes executed fills from prior ET calendar days", async () => {
    mockGetDb(
      [],
      [
        { payload: JSON.stringify(execPayload("today")), fill_time: todayFillIso },
        {
          payload: JSON.stringify(execPayload("yesterday", "2026-07-08T10:31:16-04:00")),
          fill_time: "2026-07-08T10:31:16-04:00",
        },
      ],
    );
    const { readOrdersFromDb } = await import("../lib/orders/readOrdersFromDb");
    const result = await readOrdersFromDb();
    expect(result!.executed_count).toBe(1);
    expect(result!.executed_orders.map((e) => e.execId)).toEqual(["today"]);
  });

  it("skips rows with unparseable payloads", async () => {
    mockGetDb(
      [
        { payload: JSON.stringify(openPayload(1)), updated_at: "2026-05-07T13:30:00Z" },
        { payload: "not json", updated_at: "2026-05-07T13:30:00Z" },
        { payload: null, updated_at: "2026-05-07T13:30:00Z" },
      ],
      [],
    );
    const { readOrdersFromDb } = await import("../lib/orders/readOrdersFromDb");
    const result = await readOrdersFromDb();
    expect(result!.open_count).toBe(1);
  });

  it("uses the latest open updated_at as last_sync", async () => {
    mockGetDb(
      [
        { payload: JSON.stringify(openPayload(1)), updated_at: "2026-05-07T12:00:00Z" },
        { payload: JSON.stringify(openPayload(2)), updated_at: "2026-05-07T15:00:00Z" },
      ],
      [],
    );
    const { readOrdersFromDb } = await import("../lib/orders/readOrdersFromDb");
    const result = await readOrdersFromDb();
    expect(result!.last_sync).toBe("2026-05-07T15:00:00Z");
  });

  it("falls back to executed fill_time when no open orders exist", async () => {
    mockGetDb(
      [],
      [{ payload: JSON.stringify(execPayload("e1")), fill_time: todayFillIso }],
    );
    const { readOrdersFromDb } = await import("../lib/orders/readOrdersFromDb");
    const result = await readOrdersFromDb();
    expect(result!.last_sync).toBe(todayFillIso);
  });

  it("syncs the embedded replica before reading (eliminates 60s lag drift)", async () => {
    const counters = mockGetDb(
      [{ payload: JSON.stringify(openPayload(1)), updated_at: "2026-05-07T13:30:00Z" }],
      [],
    );
    const { readOrdersFromDb } = await import("../lib/orders/readOrdersFromDb");
    await readOrdersFromDb();
    expect(counters.syncCalls).toBe(1);
  });
});
