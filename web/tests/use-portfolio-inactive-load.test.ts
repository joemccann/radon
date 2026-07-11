/**
 * @vitest-environment jsdom
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { usePortfolio } from "../lib/usePortfolio";
import type { PortfolioData } from "../lib/types";

const PORTFOLIO_PAYLOAD: PortfolioData = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-03-22T09:00:00Z",
  positions: [],
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  account_summary: {
    net_liquidation: 100_000,
    daily_pnl: 0,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 100_000,
    dividends: 0,
  },
};

function jsonResponse(body: PortfolioData) {
  return {
    ok: true,
    async json() {
      return body;
    },
  };
}

describe("usePortfolio inactive initial load", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads cached portfolio data once even when inactive", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(PORTFOLIO_PAYLOAD));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => usePortfolio(false));

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.data?.bankroll).toBe(100_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/portfolio",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("triggers the first cached read when a previously inactive portfolio hook becomes active", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(PORTFOLIO_PAYLOAD))
      .mockResolvedValueOnce(jsonResponse({
        ...PORTFOLIO_PAYLOAD,
        bankroll: 120_000,
        last_sync: "2026-03-22T09:01:00Z",
        account_summary: {
          ...PORTFOLIO_PAYLOAD.account_summary!,
          net_liquidation: 120_000,
          settled_cash: 120_000,
          buying_power: 120_000,
          excess_liquidity: 120_000,
        },
      }));
    vi.stubGlobal("fetch", fetchMock);

    const { result, rerender } = renderHook(
      ({ active }: { active: boolean }) => usePortfolio(active),
      { initialProps: { active: false } },
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data?.bankroll).toBe(100_000);

    rerender({ active: true });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/portfolio",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect((fetchMock.mock.calls[1][1] as RequestInit).method).toBeUndefined();
  });
});
