/**
 * @vitest-environment jsdom
 *
 * §3.6: the instrument-detail modal (and its close-out ticket) must follow
 * the LIVE leg, with a stable identity across mark-only refreshes.
 *   - MobilePositionList stored the tapped leg object, so a partial close or
 *     a closed position kept feeding stale contracts / direction into the
 *     ticket's risk input.
 *   - PositionTable re-resolved the leg but handed a fresh object on every
 *     portfolio poll, so dependents keyed on it (the ticket's quantity seed)
 *     reset on every refresh.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import MobilePositionList from "@/components/mobile/MobilePositionList";
import PositionTable from "@/components/PositionTable";
import type { PortfolioLeg, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { instrumentIdentityFor, resolveActiveInstrument } from "@/lib/activeInstrument";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const modalLegs: PortfolioLeg[] = [];
vi.mock("@/components/InstrumentDetailModal", () => ({
  default: (props: { leg: PortfolioLeg }) => {
    modalLegs.push(props.leg);
    return (
      <div data-testid="instrument-detail-modal">
        {props.leg.direction} {props.leg.contracts}x
      </div>
    );
  },
}));

afterEach(() => {
  cleanup();
  modalLegs.length = 0;
});

const PRICES: Record<string, PriceData> = {};

function position(over: { shortContracts?: number; shortMark?: number } = {}): PortfolioPosition {
  const shortContracts = over.shortContracts ?? 25;
  return {
    id: 1, ticker: "EWY", structure: "Covered Call $165.0 (2500 shares)", structure_type: "Covered Call",
    risk_profile: "defined", expiry: "2026-08-07", contracts: 2500, direction: "LONG",
    entry_cost: 412139, max_risk: 412139, market_value: 385500, kelly_optimal: null,
    target: null, stop: null, entry_date: "2026-07-28",
    legs: [
      { direction: "LONG", contracts: 2500, type: "Stock", strike: null, entry_cost: 424657, avg_cost: 169.86, market_price: 157.9, market_value: 394750 },
      { direction: "SHORT", contracts: shortContracts, type: "Call", strike: 165, entry_cost: 12518, avg_cost: -500.7, market_price: over.shortMark ?? 4.0, market_value: 10000 },
    ],
  };
}

const lastLeg = () => modalLegs[modalLegs.length - 1];

describe("MobilePositionList instrument modal follows the live leg", () => {
  function renderList(positions: PortfolioPosition[]) {
    return render(<MobilePositionList positions={positions} prices={PRICES} />);
  }

  it("a partial close updates the modal's leg; a closed position closes it", () => {
    const view = renderList([position()]);
    fireEvent.click(screen.getByLabelText("EWY Covered Call $165.0 (2500 shares)"));
    fireEvent.click(screen.getByText(/SHORT 25x Call \$165/));
    expect(lastLeg().contracts).toBe(25);

    view.rerender(<MobilePositionList positions={[position({ shortContracts: 10 })]} prices={PRICES} />);
    expect(lastLeg().contracts).toBe(10);
    expect(screen.getByTestId("instrument-detail-modal").textContent).toContain("SHORT 10x");

    view.rerender(<MobilePositionList positions={[]} prices={PRICES} />);
    expect(screen.queryByTestId("instrument-detail-modal")).toBeNull();
  });

  it("a mark-only refresh keeps the same leg object", () => {
    const view = renderList([position()]);
    fireEvent.click(screen.getByLabelText("EWY Covered Call $165.0 (2500 shares)"));
    fireEvent.click(screen.getByText(/SHORT 25x Call \$165/));
    const opened = lastLeg();
    view.rerender(<MobilePositionList positions={[position({ shortMark: 4.2 })]} prices={PRICES} />);
    expect(lastLeg()).toBe(opened);
  });
});

describe("instrumentIdentityFor without a broker conId", () => {
  it("follows the contract, never whatever leg now sits at the old index", () => {
    const original = position();
    const identity = instrumentIdentityFor(original.legs[0], original);
    expect(resolveActiveInstrument([original], identity)?.leg.type).toBe("Stock");
    // The stock leg is gone; index 0 is now the short call.
    const refreshed = { ...original, legs: [original.legs[1]] };
    expect(resolveActiveInstrument([refreshed], identity)).toBeNull();
  });
});

describe("PositionTable instrument modal keeps a stable leg identity", () => {
  it("mark-only refreshes keep the leg object; a size change replaces it", () => {
    const view = render(<PositionTable positions={[position()]} prices={PRICES} />);
    fireEvent.click(screen.getByLabelText("Expand legs for EWY"));
    fireEvent.click(screen.getByText("SHORT Call $165"));
    const opened = lastLeg();
    expect(opened.contracts).toBe(25);

    view.rerender(<PositionTable positions={[position({ shortMark: 4.2 })]} prices={PRICES} />);
    expect(lastLeg()).toBe(opened);

    view.rerender(<PositionTable positions={[position({ shortContracts: 10 })]} prices={PRICES} />);
    expect(lastLeg()).not.toBe(opened);
    expect(lastLeg().contracts).toBe(10);
  });
});
