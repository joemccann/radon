import { describe, expect, it } from "vitest";
import { formatOrderError, formatOrderErrorMessage } from "../lib/orderError";

describe("formatOrderError", () => {
  it("strips transport wrappers and rewrites IB margin rejection to concise operator copy", () => {
    const raw = "Radon API 502: IB error 201: Order rejected - reason:YOUR ORDER IS NOT ACCEPTED. IN ORDER TO OBTAIN THE DESIRED POSITION YOUR PREVIOUS DAY EQUITY WITH LOAN VALUE <E> (644770.54 USD) MUST EXCEED THE INITIAL MARGIN (67243.00 USD).";

    expect(formatOrderError(raw)).toEqual({
      summary: "Order rejected by IB: insufficient margin.",
      details: [
        "Previous-day equity with loan value is $644,770.54; initial margin required is $67,243.00.",
      ],
    });
  });

  it("rewrites a missing working-order lookup to operator copy", () => {
    expect(formatOrderError("Radon API 502: Trade not found (orderId=291, permId=1857171561)")).toEqual({
      summary: "Order is no longer working at IB.",
      details: ["It may have filled or been cancelled."],
    });
    expect(formatOrderError("This DAY order is no longer working at IB. DAY orders end at the regular-session close.")).toEqual({
      summary: "Order is no longer working at IB.",
      details: ["DAY orders end at the regular-session close."],
    });
  });

  it("collapses generic IB cancellation text to a readable sentence", () => {
    expect(formatOrderError("Radon API 502: Order rejected by IB: Cancelled")).toEqual({
      summary: "Order rejected by IB.",
      details: ["Cancelled."],
    });
  });

  it("keeps the legacy string formatter aligned with the structured formatter", () => {
    expect(formatOrderErrorMessage("Radon API 502: Order rejected by IB: no acknowledgement (Unknown)")).toBe(
      "Order was not acknowledged by IB.",
    );
  });

  it("converts literal <br> tokens in IB rejection text into real newlines", () => {
    const raw =
      "Radon API 502: Order rejected by IB: Cannot have open orders on both sides of the same US Option contract. You are attempting to add an order for a<br> contract where an open order already exists on the opposite side of the market. Customers are prevented, by <br>regulation, from entering a buy and a sell order for the same option contract. The right to be on both sides <br>of the market is reserved for market makers.";

    const result = formatOrderError(raw);

    expect(result.summary).toBe("Order rejected by IB.");
    expect(result.details).toHaveLength(1);
    const detail = result.details[0];
    expect(detail).not.toMatch(/<br/i);
    expect(detail).toContain("\n");
    expect(detail).toContain("for a\ncontract where an open order");
    expect(detail).toContain("by\nregulation, from entering");
    expect(detail).toContain("on both sides\nof the market");
  });

  it("handles every <br> variant (<br>, <br/>, <br />, <BR>) as a newline", () => {
    const raw = "Order rejected by IB: line one<br>line two<br/>line three<br />line four<BR>line five";
    const result = formatOrderError(raw);
    expect(result.details[0]).not.toMatch(/<br/i);
    expect(result.details[0]).toContain("line one\nline two\nline three\nline four\nline five");
  });

  it("preserves pre-existing newlines while normalising <br>", () => {
    const raw = "Order rejected by IB: alpha\nbeta<br>gamma";
    const result = formatOrderError(raw);
    expect(result.details[0]).toContain("alpha\nbeta\ngamma");
  });
});
