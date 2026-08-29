// @vitest-environment jsdom
//
// R-278 / R-281 (REL-095): the mobile ticket reaches desktop parity on the
// two safety surfaces.
//
// (a) `payoffLegs` was built from the RAW legs, so a 10-lot carried its
//     quantity into the curve AND was multiplied by `totalQty` again at the
//     at-zero figure. The desktop builds the same array from
//     `normalizeComboOrder(legs).legs` so the curve describes ONE combo,
//     which is what the "RISK · PER 1× COMBO" heading claims.
//
// (b) used to live here as a grep over the handler's source text. It moved to
//     the behavioural gate tests (`mobile-ticket-transmit-gate` /
//     `chain-transmit-gate`), which drive the handler and assert the wire: the
//     production defect CLAUDE.md cites (2026-08-27) had the guard TEXT
//     present and still shipped, because the handler was memoised without
//     `transmitArmed` in its deps and closed over a stale `false`.

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import MobileOrderTicket from "@/components/mobile/MobileOrderTicket";
import { normalizeComboOrder } from "@/lib/optionsChainUtils";
import { netPremiumForPayoff, payoffAtExpiry, payoffCurve } from "@/lib/order/payoff";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioData } from "@/lib/types";

vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActions: () => ({ pushNotification: vi.fn() }),
  useOrderActionsOptional: () => ({ pushNotification: vi.fn() }),
}));

const EXPIRY = (() => {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + 21);
  return d.toISOString().slice(0, 10);
})();
const COMPACT = EXPIRY.replaceAll("-", "");
const CALL_KEY = `MU_${COMPACT}_100_C`;

function quote(symbol: string, bid: number, ask: number): PriceData {
  return {
    symbol, last: (bid + ask) / 2, lastIsCalculated: false, bid, ask,
    bidSize: 10, askSize: 10, volume: 100, high: ask, low: bid, open: bid, close: bid,
    week52High: null, week52Low: null, avgVolume: null, delta: null, gamma: null,
    theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: new Date().toISOString(),
  } as PriceData;
}

const PRICES: Record<string, PriceData> = {
  MU: quote("MU", 99.5, 100.0),
  [CALL_KEY]: quote(CALL_KEY, 1.95, 2.05),
};

const PORTFOLIO = {
  bankroll: 1_000_000, peak_value: 1_000_000, last_sync: new Date().toISOString(),
  total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100,
  position_count: 0, defined_risk_count: 0, undefined_risk_count: 0,
  avg_kelly_optimal: null, positions: [],
} as unknown as PortfolioData;

/** 10-lot naked short $100 call at 2.00 credit: unbounded, so the gate engages. */
const SHORT_CALL_10 = [
  {
    id: "leg-1",
    action: "SELL" as const,
    right: "C" as const,
    strike: 100,
    expiry: COMPACT,
    quantity: 10,
    limitPrice: 2.0,
  },
];

/** Short strangle: unbounded on the call side, real loss at zero on the put. */
const SHORT_STRANGLE_10 = [
  {
    id: "leg-p",
    action: "SELL" as const,
    right: "P" as const,
    strike: 95,
    expiry: COMPACT,
    quantity: 10,
    limitPrice: 2.0,
  },
  {
    id: "leg-c",
    action: "SELL" as const,
    right: "C" as const,
    strike: 100,
    expiry: COMPACT,
    quantity: 10,
    limitPrice: 2.0,
  },
];

function renderTicket(legs = SHORT_CALL_10) {
  return render(
    <MobileOrderTicket
      open
      ticker="MU"
      legs={legs}
      prices={PRICES}
      portfolio={PORTFOLIO}
      onClose={vi.fn()}
      onPlaced={vi.fn()}
    />,
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }))),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("mobile ticket payoff is per-1x, like the desktop rail", () => {
  it("prices the breakeven of a 10-lot short call per ONE combo", async () => {
    renderTicket();
    fireEvent.click(await screen.findByTestId("mobile-order-ticket-review"));

    const cell = await waitFor(() => {
      const label = [...document.querySelectorAll(".ticket-risk-cell-label")].find(
        (n) => n.textContent === "BREAKEVENS",
      );
      expect(label).toBeTruthy();
      return label!.parentElement!;
    });

    // Short $100 call at 2.00 credit breaks even at 102.00 per contract.
    // Pre-fix the curve carried quantity 10, so the 2.00 credit was consumed
    // by 10 contracts of intrinsic and the breakeven printed 100.20.
    expect(cell.textContent).toContain("102.00");
    expect(cell.textContent).not.toContain("100.20");
  });

  it("matches payoffCurve over normalizeComboOrder legs, so the two cannot drift", async () => {
    renderTicket();
    fireEvent.click(await screen.findByTestId("mobile-order-ticket-review"));

    const normalized = normalizeComboOrder(SHORT_CALL_10).legs.map((leg) => ({
      action: leg.action as "BUY" | "SELL",
      right: leg.right as "C" | "P",
      strike: leg.strike,
      quantity: leg.quantity,
    }));
    const premium = netPremiumForPayoff(normalized, false, 2.0);
    const expected = payoffCurve(normalized, premium, { spot: 99.75 }).breakevens
      .map((b) => b.toFixed(2))
      .join(" / ");

    const cell = await waitFor(() => {
      const label = [...document.querySelectorAll(".ticket-risk-cell-label")].find(
        (n) => n.textContent === "BREAKEVENS",
      );
      expect(label).toBeTruthy();
      return label!.parentElement!;
    });
    expect(cell.textContent).toContain(expected);
  });

  // A short strangle is the case that exercises the at-zero figure: the call
  // side makes max loss unbounded (so the sentence renders at all) and the put
  // side makes the loss at zero real. A bare short call GAINS at zero, so its
  // `atZero < 0` branch never fires.
  it("states the at-zero loss once, not quantity-squared", async () => {
    renderTicket(SHORT_STRANGLE_10);
    fireEvent.click(await screen.findByTestId("mobile-order-ticket-review"));
    const ack = await screen.findByTestId("ticket-unbounded-ack");
    const sentence = ack.closest("label")!.textContent ?? "";

    const normalized = normalizeComboOrder(SHORT_STRANGLE_10).legs.map((leg) => ({
      action: leg.action as "BUY" | "SELL",
      right: leg.right as "C" | "P",
      strike: leg.strike,
      quantity: leg.quantity,
    }));
    const qty = normalizeComboOrder(SHORT_STRANGLE_10).quantity;
    const premium = netPremiumForPayoff(normalized, true, -4);
    const perCombo = Math.abs(payoffAtExpiry(normalized, premium, 0)) * 100 * qty;
    const fmt = (n: number) => `$${Math.round(n).toLocaleString("en-US")}`;

    // The library figure appears once. Pre-fix the curve already carried the
    // 10-lot quantity and `* (totalQty || 1)` applied it a second time.
    expect(sentence).toContain(fmt(perCombo));
    expect(sentence).not.toContain(fmt(perCombo * qty));
  });
});
