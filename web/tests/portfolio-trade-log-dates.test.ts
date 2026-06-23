/**
 * @vitest-environment node
 *
 * Verifies /api/portfolio derives `trade_log_dates` from Turso `journal`
 * only. It must not fall back to `data/trade_log.json`.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockReadFile = vi.fn();
vi.mock("fs/promises", () => ({ readFile: mockReadFile }));

const mockReadDataFile = vi.fn();
vi.mock("@tools/data-reader", () => ({ readDataFile: mockReadDataFile }));

const mockRadonFetch = vi.fn().mockResolvedValue({ ok: true });
vi.mock("@/lib/radonApi", () => ({ radonFetch: mockRadonFetch }));

const mockExecute = vi.fn();
vi.mock("@/lib/db", () => ({ getDb: () => ({ execute: mockExecute }) }));

function makePortfolio(lastSync = "2026-05-07T12:00:00Z") {
  return {
    bankroll: 100_000,
    peak_value: 100_000,
    last_sync: lastSync,
    positions: [],
    total_deployed_pct: 0,
    total_deployed_dollars: 0,
    remaining_capacity_pct: 100,
    position_count: 0,
    defined_risk_count: 0,
    undefined_risk_count: 0,
    avg_kelly_optimal: null,
  };
}

function mockDb({
  portfolio = makePortfolio(),
  journalRows = [],
  journalThrows = false,
}: {
  portfolio?: Record<string, unknown> | null;
  journalRows?: Array<Record<string, string>>;
  journalThrows?: boolean;
} = {}) {
  mockExecute.mockImplementation(async ({ sql }: { sql: string }) => {
    if (/FROM\s+portfolio_snapshots/i.test(sql)) {
      return {
        rows: portfolio
          ? [{ taken_at: portfolio.last_sync, payload: JSON.stringify(portfolio) }]
          : [],
      };
    }
    if (/FROM\s+journal/i.test(sql)) {
      if (journalThrows) throw new Error("WAL locked");
      return { rows: journalRows };
    }
    return { rows: [] };
  });
}

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  mockDb();
});

describe("GET /api/portfolio — trade_log_dates source", () => {
  it("derives trade_log_dates from the journal table", async () => {
    mockDb({
      journalRows: [
        { ticker: "INTC", date: "2026-05-05" },
        { ticker: "TSLA", date: "2026-04-22" },
      ],
    });
    mockReadFile.mockResolvedValue(
      JSON.stringify({ trades: [{ ticker: "INTC", date: "2024-01-01" }] }),
    );

    const { GET } = await import("../app/api/portfolio/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.trade_log_dates).toEqual({
      INTC: "2026-05-05",
      TSLA: "2026-04-22",
    });
    expect(mockReadFile).not.toHaveBeenCalled();
    expect(mockReadDataFile).not.toHaveBeenCalled();
  });

  it("returns an empty trade_log_dates map when the journal table is empty", async () => {
    mockDb({ journalRows: [] });
    mockReadFile.mockResolvedValue(
      JSON.stringify({ trades: [{ ticker: "AMD", date: "2026-04-10" }] }),
    );

    const { GET } = await import("../app/api/portfolio/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.trade_log_dates).toEqual({});
    expect(mockReadFile).not.toHaveBeenCalled();
  });

  it("returns an empty trade_log_dates map when the journal query throws", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    mockDb({ journalThrows: true });
    mockReadFile.mockResolvedValue(
      JSON.stringify({ trades: [{ ticker: "PLTR", date: "2026-04-23" }] }),
    );

    const { GET } = await import("../app/api/portfolio/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.trade_log_dates).toEqual({});
    expect(mockReadFile).not.toHaveBeenCalled();
  });
});
