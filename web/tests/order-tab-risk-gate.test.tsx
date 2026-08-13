import { describe, expect, it } from "vitest";
import { orderTabSubmitPermitted } from "@/components/ticker-detail/OrderTab";

describe("OrderTab risk permit", () => {
  it("new and combo submit require resolved risk permit", () => {
    expect(orderTabSubmitPermitted(true, false, null, null)).toBe(false);
    expect(orderTabSubmitPermitted(true, false, null, { okToSubmit: false })).toBe(false);
    expect(orderTabSubmitPermitted(true, false, null, { okToSubmit: true })).toBe(true);
    expect(orderTabSubmitPermitted(true, false, "blocked", { okToSubmit: true })).toBe(false);
  });
});
