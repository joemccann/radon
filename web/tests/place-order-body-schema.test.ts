import { describe, expect, it } from "vitest";
import {
  firstPlaceOrderSchemaErrorMessage,
  normalizeOptionRight,
} from "../lib/placeOrderBodySchema";

describe("placeOrderBodySchema", () => {
  it("accepts minimal stock order body", () => {
    expect(
      firstPlaceOrderSchemaErrorMessage({
        symbol: "AAPL",
        action: "BUY",
        quantity: 1,
        limitPrice: 100,
      }),
    ).toBeNull();
  });

  it("rejects missing symbol with field hint", () => {
    const msg = firstPlaceOrderSchemaErrorMessage({
      action: "BUY",
      quantity: 1,
      limitPrice: 100,
    });
    expect(msg).toBeTruthy();
    expect(msg!.toLowerCase()).toContain("symbol");
  });

  it("rejects malformed combo leg", () => {
    const msg = firstPlaceOrderSchemaErrorMessage({
      type: "combo",
      symbol: "AAPL",
      action: "BUY",
      quantity: 1,
      limitPrice: 1,
      legs: [{ expiry: "20260417", strike: 100, right: "C", action: "BUY", ratio: "x" }],
    });
    expect(msg).toBeTruthy();
  });

  it("accepts chain-style combo legs (CALL/PUT + symbol/secType)", () => {
    expect(
      firstPlaceOrderSchemaErrorMessage({
        type: "combo",
        symbol: "AAPL",
        action: "BUY",
        quantity: 1,
        limitPrice: 1.5,
        legs: [
          {
            symbol: "AAPL",
            secType: "OPT",
            expiry: "20260417",
            strike: 100,
            right: "CALL",
            action: "BUY",
            ratio: 1,
          },
          {
            symbol: "AAPL",
            secType: "OPT",
            expiry: "20260417",
            strike: 110,
            right: "CALL",
            action: "SELL",
            ratio: 1,
          },
        ],
      }),
    ).toBeNull();
  });

  it("accepts option order with CALL/PUT right", () => {
    expect(
      firstPlaceOrderSchemaErrorMessage({
        type: "option",
        symbol: "AAPL",
        action: "BUY",
        quantity: 1,
        limitPrice: 5,
        expiry: "20260417",
        strike: 200,
        right: "PUT",
      }),
    ).toBeNull();
  });

  it("normalizeOptionRight maps CALL/PUT to C/P", () => {
    expect(normalizeOptionRight("CALL")).toBe("C");
    expect(normalizeOptionRight("PUT")).toBe("P");
    expect(normalizeOptionRight("C")).toBe("C");
    expect(normalizeOptionRight("P")).toBe("P");
  });
});
