/**
 * /api/internals reads Turso (cri_snapshots, menthorq_cta) and FastAPI's skew
 * history, with the last good read of each held in memory. It never reads data/.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ execute: vi.fn(), radonFetch: vi.fn() }));

vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => ({ execute: mocks.execute }) }));
vi.mock("@/lib/radonApi", () => ({ radonFetch: mocks.radonFetch }));
vi.mock("fs/promises", () => ({
  writeFile: vi.fn().mockResolvedValue(undefined),
  mkdir: vi.fn().mockResolvedValue(undefined),
}));

const history = Array.from({ length: 20 }, (_, index) => ({
  date: `2026-02-${String(index + 1).padStart(2, "0")}`,
  realized_vol: 10 + index,
}));

function menthorqPayload(date: string, nq: number, spx: number) {
  return JSON.stringify({
    date,
    tables: {
      index: [
        { underlying: "Nasdaq 100", position_today: nq },
        { underlying: "S&P 500", position_today: spx },
      ],
    },
  });
}

function tursoRows({ sql }: { sql: string }) {
  if (sql.includes("cri_snapshots")) {
    return {
      rows: [{
        taken_at: new Date().toISOString(),
        payload: JSON.stringify({ date: new Date().toISOString().slice(0, 10), realized_vol: 16.7, history }),
      }],
    };
  }
  if (sql.includes("menthorq_cta") && sql.includes("LIMIT ?")) {
    // Newest first, as the route's ORDER BY date DESC returns them.
    return {
      rows: [
        { date: "2026-02-03", payload: menthorqPayload("2026-02-03", 1.5, 1) },
        { date: "2026-02-02", payload: menthorqPayload("2026-02-02", 1.2, 1) },
        { date: "2026-02-01", payload: "not JSON" },
      ],
    };
  }
  if (sql.includes("menthorq_cta")) {
    return { rows: [{ date: "2026-02-03", payload: menthorqPayload("2026-02-03", 1.5, 1) }] };
  }
  throw new Error(`unexpected query: ${sql}`);
}

beforeEach(() => {
  vi.resetModules();
  mocks.execute.mockReset().mockImplementation(async (statement) => tursoRows(statement));
  mocks.radonFetch.mockReset().mockRejectedValue(new Error("FastAPI down"));
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /api/internals", () => {
  it("builds the CTA position history from menthorq_cta, oldest first, skipping a corrupt row", async () => {
    const { GET } = await import("../app/api/internals/route");
    const body = await (await GET()).json();

    expect(body.realized_vol).toBe(16.7);
    expect(body.cta_position_spread).toBeCloseTo(0.5);
    expect(body.cta_position_history.map((point: { date: string }) => point.date)).toEqual([
      "2026-02-02",
      "2026-02-03",
    ]);
  });

  it("serves CRI and CTA from memory when Turso later fails", async () => {
    const { GET } = await import("../app/api/internals/route");
    await GET();
    mocks.execute.mockRejectedValue(new Error("offline"));

    const body = await (await GET()).json();
    expect(body.realized_vol).toBe(16.7);
    expect(body.cta_position_spread).toBeCloseTo(0.5);
    expect(body.cta_position_history).toHaveLength(2);
  });

  const skewResponse = (date: string) => ({
    nq: { ticker: "NDX", expiry: null, delta: 25, timeframe: "5Y", data: [{ date, value: 2.5 }] },
    spx: { ticker: "SPX", expiry: null, delta: 25, timeframe: "5Y", data: [{ date, value: 1.5 }] },
  });
  const skewFetches = () =>
    mocks.radonFetch.mock.calls.filter(([path]) => path === "/internals/skew-history").length;

  it("stays off the network while the skew history in memory is current", async () => {
    const today = new Date().toLocaleDateString("sv", { timeZone: "America/New_York" });
    mocks.radonFetch.mockResolvedValueOnce(skewResponse(today));
    const { GET } = await import("../app/api/internals/route");
    const first = await (await GET()).json();
    expect(first.nq_skew_history).toHaveLength(1);
    expect(skewFetches()).toBe(1);

    const second = await (await GET()).json();
    expect(second.nq_skew_history).toEqual(first.nq_skew_history);
    expect(skewFetches()).toBe(1);
  });

  it("refetches a stale skew history and keeps serving it when FastAPI fails", async () => {
    mocks.radonFetch.mockResolvedValueOnce(skewResponse("2026-02-03"));
    const { GET } = await import("../app/api/internals/route");
    const first = await (await GET()).json();
    expect(first.nq_skew_history).toHaveLength(1);

    const second = await (await GET()).json();
    expect(skewFetches()).toBe(2);
    expect(second.nq_skew_history).toEqual(first.nq_skew_history);
  });
});
