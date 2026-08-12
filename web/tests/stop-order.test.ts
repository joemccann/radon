import { describe, expect, it } from "vitest";
import {
  ibPlaceFields,
  paperPlaceFields,
  parseIbOrderType,
  pricesValidForOrderType,
  riskPriceForOrderType,
} from "../lib/order/stopOrder";

describe("parseIbOrderType", () => {
  it("maps aliases to STP / STP LMT / LMT", () => {
    expect(parseIbOrderType("STP")).toBe("STP");
    expect(parseIbOrderType("stop")).toBe("STP");
    expect(parseIbOrderType("STP LMT")).toBe("STP LMT");
    expect(parseIbOrderType("stop-limit")).toBe("STP LMT");
    expect(parseIbOrderType(undefined)).toBe("LMT");
  });
});

describe("pricesValidForOrderType", () => {
  it("STP needs only a positive stop", () => {
    expect(pricesValidForOrderType({ orderType: "STP", limitPrice: NaN, stopPrice: 170 })).toBe(true);
    expect(pricesValidForOrderType({ orderType: "STP", limitPrice: 1, stopPrice: 0 })).toBe(false);
  });

  it("STP LMT needs stop and limit", () => {
    expect(pricesValidForOrderType({
      orderType: "STP LMT", limitPrice: 168, stopPrice: 170,
    })).toBe(true);
    expect(pricesValidForOrderType({
      orderType: "STP LMT", limitPrice: NaN, stopPrice: 170,
    })).toBe(false);
  });

  it("LMT still requires a positive limit", () => {
    expect(pricesValidForOrderType({ orderType: "LMT", limitPrice: 10, stopPrice: NaN })).toBe(true);
    expect(pricesValidForOrderType({ orderType: "LMT", limitPrice: 0, stopPrice: 10 })).toBe(false);
  });
});

describe("place field slices", () => {
  it("STP omits limitPrice", () => {
    expect(ibPlaceFields("STP", 168, 170)).toEqual({ orderType: "STP", stopPrice: 170 });
  });

  it("paper maps both stop types to STOP", () => {
    expect(paperPlaceFields("STP", 168, 170)).toEqual({
      order_type: "STOP",
      stop_price: 170,
    });
  });

  it("risk uses stop for STP and limit otherwise", () => {
    expect(riskPriceForOrderType("STP", 168, 170)).toBe(170);
    expect(riskPriceForOrderType("STP LMT", 168, 170)).toBe(168);
  });
});
