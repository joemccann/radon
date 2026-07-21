/**
 * @vitest-environment jsdom
 *
 * P0 safety regressions for the mobile order builder (Phase 1-3).
 *
 * `MobileOrderTicket` is the mobile order-entry surface. It MUST:
 *  1. Require a two-step confirm before it POSTs to /api/orders/place.
 *  2. Disable the confirm action on an unbounded / undefined-risk order
 *     (the OrderRiskGate chokepoint drives `okToSubmit`).
 *  3. Label a single-leg close as "SELL TO CLOSE" / "BUY TO CLOSE" when the
 *     portfolio holds a covering position.
 *  4. Render IB rejection text through OrderErrorBanner/formatOrderError —
 *     never leaking literal "<br>" tokens.
 *  5. Fill the limit input from the Bid/Mid/Ask quote chips.
 *
 * Only ComboSkewPanel is mocked (it is unrelated skew UI). The OrderRiskGate,
 * useOrderRisk, OrderErrorBanner and BottomSheet all run for real so the risk
 * math + error formatting are exercised end-to-end.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import MobileOrderTicket from "../components/mobile/MobileOrderTicket";
import type { OrderLeg } from "@/lib/optionsChainUtils";
import type { PortfolioData } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

// ComboSkewPanel is unrelated skew analytics; stub it so the ticket renders in
// isolation without pulling the skew compute path into these order-safety tests.
vi.mock("@/components/ComboSkewPanel", () => ({ default: () => null }));

const TICKER = "AAPL";
const EXPIRY = "20260320";

/** A live quote for the 200 Call: bid 3.00 / ask 3.40 → mid 3.20. */
function makePrice(symbol: string, bid: number, ask: number): PriceData {
  return {
    symbol,
    last: (bid + ask) / 2,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 1,
    askSize: 1,
    volume: 100,
    high: null,
    low: null,
    open: null,
    close: (bid + ask) / 2,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: new Date().toISOString(),
  };
}

// 200 Call quote → chip magnitudes bid 3.00 / mid 3.20 / ask 3.40.
const PRICES: Record<string, PriceData> = {
  [`${TICKER}_${EXPIRY}_200_C`]: makePrice(`${TICKER}_${EXPIRY}_200_C`, 3.0, 3.4),
  [`${TICKER}_${EXPIRY}_965_P`]: makePrice(`${TICKER}_${EXPIRY}_965_P`, 2.9, 3.1),
};

const EMPTY_PORTFOLIO = { positions: [] } as unknown as PortfolioData;

function makeLeg(overrides: Partial<OrderLeg> = {}): OrderLeg {
  return {
    id: `${TICKER}_${EXPIRY}_200_C`,
    action: "BUY",
    right: "C",
    strike: 200,
    expiry: EXPIRY,
    quantity: 1,
    limitPrice: null,
    ...overrides,
  };
}

function renderTicket(props: {
  legs: OrderLeg[];
  portfolio?: PortfolioData | null;
  prices?: Record<string, PriceData>;
}) {
  return render(
    <MobileOrderTicket
      open
      ticker={TICKER}
      legs={props.legs}
      prices={props.prices ?? PRICES}
      spot={210}
      portfolio={props.portfolio ?? EMPTY_PORTFOLIO}
      onClose={() => {}}
      onRemoveLeg={() => {}}
      onUpdateLeg={() => {}}
      onClearLegs={() => {}}
    />,
  );
}

/** Portfolio holding a covering position, cast to the full shape (only
 *  positions[].{ticker,expiry,legs} are read by the close-out detector). */
function portfolioHolding(leg: {
  direction: "LONG" | "SHORT";
  type: "Call" | "Put";
  strike: number;
  contracts: number;
  avgCost: number;
}): PortfolioData {
  return {
    positions: [
      {
        id: 1,
        ticker: TICKER,
        structure: `${leg.direction} ${leg.type}`,
        structure_type: `${leg.direction === "LONG" ? "Long" : "Short"} ${leg.type}`,
        risk_profile: "defined",
        expiry: "2026-03-20",
        contracts: leg.contracts,
        direction: leg.direction,
        entry_cost: leg.contracts * leg.avgCost * (leg.direction === "SHORT" ? -1 : 1),
        max_risk: null,
        market_value: null,
        kelly_optimal: null,
        target: null,
        stop: null,
        legs: [
          {
            direction: leg.direction,
            contracts: leg.contracts,
            type: leg.type,
            strike: leg.strike,
            entry_cost: leg.contracts * leg.avgCost,
            avg_cost: leg.avgCost,
            market_price: null,
            market_value: null,
          },
        ],
      },
    ],
  } as unknown as PortfolioData;
}

/** A SELL leg on an unheld underlying fires useShortAvailability against
 *  /api/short-availability/{ticker}; that route returns ShortAvailabilityData,
 *  not the order-place shape. Route the mock so LocateFeeChip renders a real
 *  payload instead of crashing on an undefined `source`. */
function shortAvailabilityPayload(ticker: string) {
  return {
    ticker,
    shortable: null,
    difficulty: null,
    shortable_shares: null,
    fee_rate: null,
    rebate_rate: null,
    source: "none" as const,
    as_of: new Date().toISOString(),
    missing: true,
  };
}

function orderFetchMock(placeResponse: { ok: boolean; json: () => Promise<unknown> }) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/short-availability/")) {
      return Promise.resolve({
        ok: true,
        json: async () => shortAvailabilityPayload(TICKER),
      });
    }
    return Promise.resolve(placeResponse);
  }) as unknown as typeof fetch;
}

beforeEach(() => {
  global.fetch = orderFetchMock({
    ok: true,
    json: async () => ({ ok: true, orderId: 1 }),
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("MobileOrderTicket — two-step confirm gates placement", () => {
  it("requires Review then Confirm before POSTing to /api/orders/place", async () => {
    const { getByTestId, queryByTestId } = renderTicket({ legs: [makeLeg()] });

    // Build view shows Review, NOT a confirm/submit button.
    expect(getByTestId("mobile-order-ticket-review")).toBeTruthy();
    expect(queryByTestId("mobile-order-ticket-submit")).toBeNull();

    // First tap → confirm step (Back + Confirm & send). Still no POST.
    fireEvent.click(getByTestId("mobile-order-ticket-review"));
    expect(getByTestId("mobile-order-ticket-back")).toBeTruthy();
    const submit = getByTestId("mobile-order-ticket-submit") as HTMLButtonElement;
    expect(submit.textContent).toMatch(/Confirm & send/);
    expect(global.fetch).not.toHaveBeenCalled();

    // Second tap → the single POST fires.
    fireEvent.click(submit);
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
    const [url, init] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/orders/place");
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.type).toBe("option");
    expect(body.action).toBe("BUY");
    expect(body.strike).toBe(200);
  });
});

describe("MobileOrderTicket — okToSubmit gating", () => {
  it("disables Confirm on an unbounded naked short, enables it on a bounded long", async () => {
    // Naked short call, empty portfolio → UNBOUNDED risk → Confirm disabled.
    const nakedShort = renderTicket({ legs: [makeLeg({ action: "SELL" })] });
    fireEvent.click(nakedShort.getByTestId("mobile-order-ticket-review"));
    const shortSubmit = nakedShort.getByTestId("mobile-order-ticket-submit") as HTMLButtonElement;
    await waitFor(() => expect(shortSubmit.disabled).toBe(true));
    cleanup();

    // Bounded long call → defined risk → Confirm enabled.
    const long = renderTicket({ legs: [makeLeg({ action: "BUY" })] });
    fireEvent.click(long.getByTestId("mobile-order-ticket-review"));
    const longSubmit = long.getByTestId("mobile-order-ticket-submit") as HTMLButtonElement;
    await waitFor(() => expect(longSubmit.disabled).toBe(false));
  });
});

describe("MobileOrderTicket — close labelling", () => {
  it("labels a SELL against a covering LONG as SELL TO CLOSE", () => {
    const portfolio = portfolioHolding({
      direction: "LONG",
      type: "Call",
      strike: 200,
      contracts: 65,
      avgCost: 150,
    });
    const { getByTestId } = renderTicket({ legs: [makeLeg({ action: "SELL" })], portfolio });
    expect(getByTestId("mobile-order-ticket-review").textContent).toMatch(/SELL TO CLOSE/);
  });

  it("labels a BUY against a covering SHORT as BUY TO CLOSE", () => {
    const portfolio = portfolioHolding({
      direction: "SHORT",
      type: "Put",
      strike: 965,
      contracts: 10,
      avgCost: 5000,
    });
    const buyPutLeg = makeLeg({
      id: `${TICKER}_${EXPIRY}_965_P`,
      action: "BUY",
      right: "P",
      strike: 965,
    });
    const { getByTestId } = renderTicket({ legs: [buyPutLeg], portfolio });
    expect(getByTestId("mobile-order-ticket-review").textContent).toMatch(/BUY TO CLOSE/);
  });
});

describe("MobileOrderTicket — IB error rendering", () => {
  it("renders a failed IB placement through formatOrderError with no literal <br>", async () => {
    global.fetch = orderFetchMock({
      ok: false,
      json: async () => ({
        error:
          "Order rejected by IB: The contract is not available for trading.<br>Please check the order parameters.",
      }),
    });

    const { getByTestId } = renderTicket({ legs: [makeLeg()] });
    fireEvent.click(getByTestId("mobile-order-ticket-review"));
    fireEvent.click(getByTestId("mobile-order-ticket-submit"));

    // The sheet portals to document.body, so query the document (not the
    // render container).
    await waitFor(() => expect(document.querySelector(".order-error")).toBeTruthy());

    const errorText = document.querySelector(".order-error")!.textContent ?? "";
    // formatOrderError strips the "Order rejected by IB:" prefix into the
    // summary and moves the reason into a detail row.
    expect(errorText).toMatch(/Order rejected by IB\./);
    expect(errorText).toMatch(/The contract is not available for trading\./);
    // The literal transport token must never reach the DOM.
    expect(errorText).not.toMatch(/<br>/);
  });
});

describe("MobileOrderTicket — quote chips", () => {
  it("fills the limit input from the Bid chip", () => {
    const { getByTestId } = renderTicket({ legs: [makeLeg()] });
    const input = getByTestId("mobile-order-ticket-price-input") as HTMLInputElement;

    // Auto-populates to mid (3.20) on first quote availability.
    expect(input.value).toBe("3.20");

    // Bid chip magnitude is 3.00 → tapping it writes 3.00 into the input.
    fireEvent.click(getByTestId("mobile-order-ticket-quote-bid"));
    expect(input.value).toBe("3.00");

    // Ask chip magnitude is 3.40.
    fireEvent.click(getByTestId("mobile-order-ticket-quote-ask"));
    expect(input.value).toBe("3.40");
  });
});

describe("MobileOrderTicket — iOS viewport sizing", () => {
  // iPhone cutoff regression (2026-07-21): an inline max-height of "82vh"
  // resolved against the LARGE iOS viewport and pushed the sticky footer
  // (Review / Confirm & send) below the visible area. The cap must be dvh and
  // must flow through --sheet-max-h so .m-sheet can clamp it by the keyboard.
  it("caps the sheet at 82dvh via --sheet-max-h", () => {
    const { getByTestId } = renderTicket({ legs: [makeLeg()] });
    const panel = getByTestId("mobile-order-ticket-panel");
    expect(panel.style.getPropertyValue("--sheet-max-h")).toBe("82dvh");
    expect(panel.style.maxHeight).toBe("");
  });
});
