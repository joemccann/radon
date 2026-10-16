/**
 * @vitest-environment jsdom
 *
 * Regression (CRWD 2026-10-14): selling part of a held long call from the
 * chain ticket rendered as "Short Call ... COVERED BY HELD LONG" with a
 * "SPREAD · INCL. HELD LEG" section. A SELL of the exact held contract is a
 * sell-to-close, not a spread; the chokepoint must classify it without the
 * surface having to declare `closeOut`.
 */
import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import type { PortfolioData } from "@/lib/types";
import { useOrderRisk } from "@/lib/order/risk";

const heldLongCalls: PortfolioData = {
  positions: [
    {
      id: 3,
      ticker: "CRWD",
      structure: "Long Call",
      structure_type: "Long Option",
      risk_profile: "DEFINED",
      expiry: "2026-10-16",
      contracts: 100,
      direction: "LONG",
      entry_cost: 130_000,
      max_risk: 130_000,
      market_value: 22_200,
      legs: [
        {
          direction: "LONG",
          contracts: 100,
          type: "Call",
          strike: 260,
          expiry: "2026-10-16",
          entry_cost: 130_000,
          avg_cost: 1_300,
          market_price: 2.22,
          market_value: 22_200,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-10-01",
    },
  ],
  bankroll: 0,
  open_risk: 0,
  open_risk_pct: 0,
  convexity_score: null,
  convexity_breakdown: null,
  account_summary: null,
} as unknown as PortfolioData;

const sellHalfFromChain = {
  ticker: "CRWD",
  chainLegs: [
    { action: "SELL" as const, right: "C" as const, strike: 260, expiry: "20261016", quantity: 50 },
  ],
  netPremium: -4,
  description: "Short Call @ $4.00",
  totalCost: -20_000,
  quote: { bid: 2.09, ask: 2.25 },
  underlyingSpot: 253.68,
};

describe("chain SELL of a held same-contract long", () => {
  it("is a sell-to-close with realised P&L, not a covered short plus spread", () => {
    const { result } = renderHook(() => useOrderRisk(sellHalfFromChain, heldLongCalls));
    const state = result.current!;

    expect(state.summary.closeOutAction).toBe("SELL");
    expect(state.summary.description).toBe("Sell to Close 50× $260 Call @ $4.00");
    expect(state.summary.totalLabel).toBe("Close Credit:");
    expect(state.summary.totalCost).toBe(20_000);
    expect(state.summary.estimatedPnl).toBe(20_000 - 50 * 1_300);
    expect(state.summary.withHeldLegs ?? null).toBeNull();
    expect(state.summary.coverageNote ?? null).toBeNull();
    expect(state.coveringLegs).toEqual([]);
    expect(state.summary.maxLossUnbounded ?? false).toBe(false);
    expect(state.okToSubmit).toBe(true);
  });

  it("selling more than held is still an opening order for the excess", () => {
    const { result } = renderHook(() =>
      useOrderRisk(
        { ...sellHalfFromChain, chainLegs: [{ ...sellHalfFromChain.chainLegs[0], quantity: 150 }] },
        heldLongCalls,
      ),
    );
    expect(result.current!.summary.closeOutAction ?? null).toBeNull();
    expect(result.current!.summary.maxLossUnbounded).toBe(true);
  });
});

// ---- Chain ticket surface -------------------------------------------------
// The hook is the chokepoint, but the operator reads the ticket: no coverage
// chip, no "SPREAD · INCL. HELD LEG" block, and the transmit button names
// the close.

import React from "react";
import { afterEach, beforeEach, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";

let searchParamsString = "";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(searchParamsString),
  usePathname: () => "/MU",
  useRouter: () => ({
    replace: vi.fn(), push: vi.fn(), prefetch: vi.fn(),
    back: vi.fn(), forward: vi.fn(), refresh: vi.fn(),
  }),
}));
vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ isWatched: () => false, toggleWatch: vi.fn() }),
}));
vi.mock("../components/PriceChart", () => ({
  default: () => React.createElement("div", { "data-testid": "price-chart" }),
}));

import { EXPIRY, PORTFOLIO, chainFetch, chainWithLegs, clickCallCell } from "./helpers/chainHarness";

const heldMu970Calls: PortfolioData = {
  ...PORTFOLIO,
  position_count: 1,
  positions: [
    {
      id: 9,
      ticker: "MU",
      structure: "Long Call",
      structure_type: "Long Option",
      risk_profile: "DEFINED",
      expiry: EXPIRY.dashed,
      contracts: 10,
      direction: "LONG",
      entry_cost: 9_000,
      max_risk: 9_000,
      market_value: 12_500,
      legs: [
        {
          direction: "LONG",
          contracts: 10,
          type: "Call",
          strike: 970,
          expiry: EXPIRY.dashed,
          entry_cost: 9_000,
          avg_cost: 900,
          market_price: 12.5,
          market_value: 12_500,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-09-01",
    },
  ],
} as unknown as PortfolioData;

describe("chain ticket for a SELL of held contracts", () => {
  beforeEach(() => {
    searchParamsString = "";
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => chainFetch(input)));
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("labels the ticket Sell to Close and drops the coverage chip and spread block", async () => {
    const builder = await chainWithLegs(() => clickCallCell(970, 0), heldMu970Calls);
    const inputs = builder.querySelectorAll<HTMLInputElement>("input[type='number']");
    fireEvent.change(inputs[0], { target: { value: "5" } });
    fireEvent.change(inputs[1], { target: { value: "12.10" } });
    await waitFor(() => expect(builder.querySelector(".ticket-risk")).toBeTruthy());

    expect(builder.querySelector("[data-testid='order-builder-coverage']")).toBeNull();
    expect(builder.querySelector("[data-testid='ticket-risk-spread']")).toBeNull();
    expect(builder.textContent).toMatch(/ORDER BUILDER : Sell to Close/);
    expect(builder.textContent).toMatch(/Close Credit: \$6,050\.00/);
    expect(builder.textContent).not.toMatch(/COVERED BY HELD/);

    fireEvent.click(screen.getByTestId("ticket-verify"));
    await waitFor(() =>
      expect((screen.getByTestId("ticket-transmit") as HTMLButtonElement).textContent)
        .toBe("Transmit Sell to Close"),
    );
    expect(builder.textContent).toMatch(/Sell to Close 5× \$970 Call @ \$12\.10/);
    expect(builder.querySelector("[data-testid='ticket-risk-spread']")).toBeNull();
    expect(builder.textContent).not.toMatch(/COVERED BY HELD|Short Call/);
  });
});
