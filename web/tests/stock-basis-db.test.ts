import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ dbExecute: vi.fn() }));
vi.mock("@/lib/dbExecute", () => ({ dbExecute: mocks.dbExecute }));

import { fetchPortfolioStockBasis } from "@/lib/portfolio/stockBasisDb";

describe("portfolio stock basis history", () => {
  beforeEach(() => mocks.dbExecute.mockReset());

  it("is account scoped, time ordered, and never last-write-wins by ticker", async () => {
    mocks.dbExecute.mockResolvedValue({ rows: [
      { taken_at: "2026-08-01T12:00:00Z", payload: JSON.stringify({ positions: [{ ticker: "MSFT", account_id: "A", legs: [{ type: "Stock", avg_cost: 400 }] }] }) },
      { taken_at: "2026-08-02T12:00:00Z", payload: JSON.stringify({ positions: [{ ticker: "MSFT", account_id: "B", legs: [{ type: "Stock", avg_cost: 500 }] }] }) },
    ] });
    const history = await fetchPortfolioStockBasis();
    expect(history["A|MSFT"]).toEqual([{ takenAt: "2026-08-01T12:00:00Z", avgCost: 400 }]);
    expect(history["B|MSFT"]).toEqual([{ takenAt: "2026-08-02T12:00:00Z", avgCost: 500 }]);
    expect(history.MSFT).toBeUndefined();
  });
});
