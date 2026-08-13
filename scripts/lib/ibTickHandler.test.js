import { describe, expect, it } from "vitest";

import { createPriceData, updatePriceFromTickPrice } from "../ib_tick_handler.js";

const BID = 1;
const ASK = 2;
const LAST = 4;

describe("derived midpoint lifecycle", () => {
  it("derived_midpoint_remains_calculated_and_tracks_book_updates", () => {
    const data = createPriceData("SPY_20260918_500_P");
    updatePriceFromTickPrice(data, BID, 10);
    updatePriceFromTickPrice(data, ASK, 12);
    expect(data.last).toBe(11);
    expect(data.lastIsCalculated).toBe(true);

    updatePriceFromTickPrice(data, BID, 14);
    expect(data.last).toBe(13);
    expect(data.lastIsCalculated).toBe(true);

    updatePriceFromTickPrice(data, LAST, 12.5);
    updatePriceFromTickPrice(data, ASK, 13);
    expect(data.last).toBe(12.5);
    expect(data.lastIsCalculated).toBe(false);
  });
});
