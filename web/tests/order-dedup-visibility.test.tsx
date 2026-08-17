/**
 * @vitest-environment jsdom
 *
 * REL-027 / R-051 — a suppressed duplicate submit must be VISIBLE and
 * distinguishable from a placement. The route flags collapsed duplicates with
 * `deduplicated: true`, but no order-entry surface read it: every surface
 * rendered the plain "Order placed" success state, so the operator believed
 * two orders were live and held half the intended position.
 *
 * Contract pinned here:
 *  - `placeOrderFeedback` (client-safe, no node imports) turns a deduplicated
 *    response into a warning-toned "NOT sent again" state,
 *  - every order-entry surface renders its place response through it,
 *  - the rendered suppressed state is visibly distinct (warning-token class,
 *    explicit copy) — proven end-to-end on MobileOrderTicket.
 */
import React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import MobileOrderTicket from "../components/mobile/MobileOrderTicket";
import type { OrderLeg } from "@/lib/optionsChainUtils";
import type { PortfolioData } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

vi.mock("@/components/ComboSkewPanel", () => ({ default: () => null }));

const WEB_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("placeOrderFeedback", () => {
  it("renders deduplicated:true as a warning-toned suppressed-submit state", async () => {
    const { placeOrderFeedback } = await import("@/lib/orders/placeOrderFeedback");
    const fb = placeOrderFeedback(
      { status: "ok", orderId: 42, deduplicated: true },
      "Order placed: BUY 100 PLTR",
    );
    expect(fb.tone).toBe("warning");
    expect(fb.deduplicated).toBe(true);
    expect(fb.message).toMatch(/duplicate submit suppressed/i);
    expect(fb.message).toMatch(/NOT sent again/i);
    expect(fb.message).toContain("#42");
    expect(fb.message).not.toMatch(/^Order placed/);
  });

  it("renders a normal placement as the plain success message", async () => {
    const { placeOrderFeedback } = await import("@/lib/orders/placeOrderFeedback");
    const fb = placeOrderFeedback(
      { status: "ok", orderId: 42 },
      "Order placed: BUY 100 PLTR",
    );
    expect(fb.tone).toBe("success");
    expect(fb.deduplicated).toBe(false);
    expect(fb.message).toBe("Order placed: BUY 100 PLTR");
  });

  it("treats a malformed body as a normal placement (never blocks the success path)", async () => {
    const { placeOrderFeedback } = await import("@/lib/orders/placeOrderFeedback");
    expect(placeOrderFeedback(null, "placed").tone).toBe("success");
    expect(placeOrderFeedback({ deduplicated: "yes" }, "placed").tone).toBe("success");
  });
});

describe("every order-entry surface renders the place response through placeOrderFeedback", () => {
  const read = (rel: string) => readFileSync(path.join(WEB_DIR, rel), "utf8");
  const calls = (src: string) => (src.match(/placeOrderFeedback\(/g) ?? []).length;

  it("OrderTab wires BOTH its call sites (single-leg + combo)", () => {
    expect(calls(read("components/ticker-detail/OrderTab.tsx"))).toBeGreaterThanOrEqual(2);
  });

  it.each([
    "components/ticker-detail/OptionsChainTab.tsx",
    "components/ticker-detail/PositionTradeTicket.tsx",
    "components/mobile/MobileOrderTicket.tsx",
    "components/ticker-detail/ListedContractOrderForm.tsx",
    "components/SingleLegOrderTicket.tsx",
    "lib/chat.ts",
  ])("%s renders its response through placeOrderFeedback", (rel) => {
    expect(calls(read(rel))).toBeGreaterThanOrEqual(1);
  });
});

// ── MobileOrderTicket end-to-end visibility ─────────────────────────────

const TICKER = "AAPL";
const EXPIRY = "20260320";

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

const PRICES: Record<string, PriceData> = {
  [`${TICKER}_${EXPIRY}_200_C`]: makePrice(`${TICKER}_${EXPIRY}_200_C`, 3.0, 3.4),
};

const EMPTY_PORTFOLIO = { positions: [] } as unknown as PortfolioData;

const LEG: OrderLeg = {
  id: `${TICKER}_${EXPIRY}_200_C`,
  action: "BUY",
  right: "C",
  strike: 200,
  expiry: EXPIRY,
  quantity: 1,
  limitPrice: null,
};

function placeFetchMock(placeBody: Record<string, unknown>) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/short-availability/")) {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          ticker: TICKER,
          shortable: null,
          difficulty: null,
          shortable_shares: null,
          fee_rate: null,
          rebate_rate: null,
          source: "none" as const,
          as_of: new Date().toISOString(),
          missing: true,
        }),
      });
    }
    return Promise.resolve({ ok: true, json: async () => placeBody });
  }) as unknown as typeof fetch;
}

function renderAndSubmitTicket() {
  const utils = render(
    <MobileOrderTicket
      open
      ticker={TICKER}
      legs={[LEG]}
      prices={PRICES}
      spot={210}
      portfolio={EMPTY_PORTFOLIO}
      onClose={() => {}}
      onRemoveLeg={() => {}}
      onUpdateLeg={() => {}}
      onClearLegs={() => {}}
    />,
  );
  fireEvent.click(utils.getByTestId("mobile-order-ticket-review"));
  fireEvent.click(utils.getByTestId("mobile-order-ticket-submit"));
  return utils;
}

describe("MobileOrderTicket — a suppressed duplicate is visibly distinct", () => {
  it("shows a warning-toned NOT-sent-again state on deduplicated:true", async () => {
    global.fetch = placeFetchMock({ status: "ok", orderId: 7, deduplicated: true });
    renderAndSubmitTicket();

    await waitFor(() =>
      expect(document.querySelector('[data-testid="mobile-order-ticket-success"]')).toBeTruthy(),
    );
    const state = document.querySelector('[data-testid="mobile-order-ticket-success"]')!;
    expect(state.textContent).toMatch(/NOT sent again/i);
    expect(state.textContent).not.toMatch(/^Placed/);
    expect(state.className).toMatch(/--dedup/);
  });

  it("keeps the plain success state for a real placement", async () => {
    global.fetch = placeFetchMock({ status: "ok", orderId: 7 });
    renderAndSubmitTicket();

    await waitFor(() =>
      expect(document.querySelector('[data-testid="mobile-order-ticket-success"]')).toBeTruthy(),
    );
    const state = document.querySelector('[data-testid="mobile-order-ticket-success"]')!;
    expect(state.textContent).toMatch(/Placed/);
    expect(state.className).not.toMatch(/--dedup/);
  });
});
