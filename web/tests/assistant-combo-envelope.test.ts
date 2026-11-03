// A combo proposal's envelope direction must match what placement transmits.
// placeProposedOrder always sends a combo as BUY with the legs carrying the
// structure, so the confirmation card (which signs the premium by the
// envelope action) must never be shown a SELL envelope.

import { describe, expect, it } from "vitest";

import { validateAssistantOrderInput } from "@/lib/assistant/loop";

function combo(over: Record<string, unknown> = {}) {
  return {
    type: "combo",
    ticker: "AAPL",
    action: "BUY",
    quantity: 2,
    limit_price: 1.25,
    legs: [
      { expiry: "20261218", strike: 200, right: "C", action: "BUY", ratio: 1 },
      { expiry: "20261218", strike: 210, right: "C", action: "SELL", ratio: 1 },
    ],
    ...over,
  };
}

describe("assistant combo envelope", () => {
  it("accepts a BUY combo envelope", () => {
    const input = validateAssistantOrderInput(combo());
    expect(input).toMatchObject({ type: "combo", action: "BUY", limit_price: 1.25 });
  });

  it("refuses a SELL combo envelope", () => {
    expect(validateAssistantOrderInput(combo({ action: "SELL" }))).toBeNull();
  });

  it("still accepts single-leg SELL orders", () => {
    expect(validateAssistantOrderInput({
      type: "stock", ticker: "AAPL", action: "SELL", quantity: 10, limit_price: 190,
    })).toMatchObject({ action: "SELL" });
  });
});
