/** @vitest-environment jsdom */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import ClearOverview from "../components/dashboard/ClearOverview";
import type { PortfolioData } from "../lib/types";
import type { PriceData } from "../lib/pricesProtocol";

vi.mock("@/lib/usePerformance", () => ({ usePerformance: () => ({ data: null, loading: false, error: null }) }));
afterEach(cleanup);

const base = { positions: [], undefined_risk_count: 1, last_sync: "2026-09-04T16:00:00Z",
  account_summary: { net_liquidation: 100_000, maintenance_margin: 24_000, excess_liquidity: 62_100, buying_power: 296_000 } } as unknown as PortfolioData;

describe("dashboard: one owner per fact, say the finding", () => {
  it("prints net dollar delta exactly once on the page", () => {
    render(<ClearOverview portfolio={base} />);
    expect(screen.getAllByText("Net dollar delta")).toHaveLength(1);
  });

  it("renders the formatted net dollar delta in the risk rail when every leg is priced", () => {
    const position = { id: 1, ticker: "XYZ", expiry: "2026-12-18", structure: "Long Put", structure_type: "Long Put", contracts: 2, market_value: 1_000,
      legs: [{ type: "Put", direction: "LONG", strike: 100, contracts: 2, market_value: 1_000 }] };
    const stock = { id: 2, ticker: "ABC", structure: "Stock", structure_type: "Stock", contracts: 1_000, legs: [{ type: "Stock", direction: "LONG", contracts: 1_000, strike: null }] };
    const portfolio = { ...base, positions: [position, stock] } as unknown as PortfolioData;
    const prices = { XYZ: { last: 100 }, XYZ_20261218_100_P: { delta: 0.7 }, ABC: { last: 150 } } as unknown as Record<string, PriceData>;
    render(<ClearOverview portfolio={portfolio} prices={prices} />);
    const rail = screen.getByRole("complementary", { name: "Risk and research" });
    // stock 1,000 x $150 = +150,000; long put contributes -6,000 (see clear-overview.test.ts)
    expect(within(rail).getByText("+$144,000")).toBeDefined();
    expect(screen.getAllByText("+$144,000")).toHaveLength(1);
  });

  it("leads the risk rail with the finding instead of a generic title", () => {
    render(<ClearOverview portfolio={base} />);
    expect(screen.queryByText("Risk at a glance")).toBeNull();
    expect(screen.queryByText(/not a maximum-loss limit/)).toBeNull();
    expect(screen.queryByText(/not a repriced stress scenario/)).toBeNull();
    const rail = screen.getByRole("region", { name: "Margin is comfortable. One position has undefined risk." });
    expect(within(rail).getByText("62.1%")).toBeDefined();
    expect(within(rail).getByText("Undefined-risk positions")).toBeDefined();
    expect(within(rail).getByRole("link", { name: "Review positions" }).getAttribute("href")).toBe("/portfolio");
    expect(within(rail).getByRole("link", { name: "Market risk" }).getAttribute("href")).toBe("/regime/cri");
  });

  it("counts undefined-risk positions in the finding", () => {
    render(<ClearOverview portfolio={{ ...base, undefined_risk_count: 0 } as PortfolioData} />);
    expect(screen.getByRole("heading", { name: "Margin is comfortable. No position has undefined risk." })).toBeDefined();
    cleanup();
    render(<ClearOverview portfolio={{ ...base, undefined_risk_count: 3 } as PortfolioData} />);
    expect(screen.getByRole("heading", { name: "Margin is comfortable. 3 positions have undefined risk." })).toBeDefined();
  });

  it("keeps the margin definition on hover", () => {
    render(<ClearOverview portfolio={base} />);
    expect(screen.getByText("Margin cushion", { selector: "dt" }).getAttribute("title")).toMatch(/Excess liquidity \/ net liquidation/);
  });
});
