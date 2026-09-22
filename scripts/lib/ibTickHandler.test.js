import { describe, expect, it } from "vitest";

import {
  createPriceData,
  parseRtVolume,
  updatePriceFromTickPrice,
  updatePriceFromTickString,
} from "../ib_tick_handler.js";

const BID = 1;
const ASK = 2;
const LAST = 4;
const RT_VOLUME = 48;
const RT_TRD_VOLUME = 77;

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

describe("parseRtVolume — generic tick 233 / tickString type 48", () => {
  it("extracts totalVolume from the documented semicolon payload", () => {
    expect(parseRtVolume("12.34;5;1694438400;1500;12.31;true")).toBe(1500);
  });

  it("treats a zero totalVolume as a real session print (untraded is 0, not null)", () => {
    expect(parseRtVolume(";;1694438400;0;;false")).toBe(0);
  });

  it("returns null for empty, short, or non-numeric volume fields", () => {
    expect(parseRtVolume("")).toBeNull();
    expect(parseRtVolume("12.34;5;1694438400")).toBeNull();
    expect(parseRtVolume("12.34;5;1694438400;;12.31;true")).toBeNull();
    expect(parseRtVolume("12.34;5;1694438400;N/A;12.31;true")).toBeNull();
    expect(parseRtVolume("not-a-payload")).toBeNull();
  });

  it("rejects a negative volume field", () => {
    expect(parseRtVolume("12.34;5;1694438400;-1;12.31;true")).toBeNull();
  });
});

describe("updatePriceFromTickString — RT_VOLUME does not invent High/Low", () => {
  it("writes volume from RT_VOLUME (48) and leaves high/low/last untouched", () => {
    const data = createPriceData("SNDK_20260925_1750_C");
    data.last = 4.2;
    data.lastIsCalculated = true;
    data.bid = 4.0;
    data.ask = 4.4;
    const updated = updatePriceFromTickString(
      data,
      RT_VOLUME,
      "4.20;1;1694438400;17;4.18;true",
    );
    expect(updated).toBe(true);
    expect(data.volume).toBe(17);
    expect(data.high).toBeNull();
    expect(data.low).toBeNull();
    expect(data.last).toBe(4.2);
    expect(data.lastIsCalculated).toBe(true);
  });

  it("writes volume from RT_TRD_VOLUME (77)", () => {
    const data = createPriceData("SNDK_20260925_1750_C");
    expect(updatePriceFromTickString(data, RT_TRD_VOLUME, ";;1694438400;3;;false")).toBe(true);
    expect(data.volume).toBe(3);
  });

  it("returns false and keeps a prior volume when the string has no volume field", () => {
    const data = createPriceData("SNDK_20260925_1750_C");
    data.volume = 9;
    expect(updatePriceFromTickString(data, RT_VOLUME, "4.20;1;1694438400")).toBe(false);
    expect(data.volume).toBe(9);
  });

  it("ignores unrelated tickString types", () => {
    const data = createPriceData("SNDK_20260925_1750_C");
    expect(updatePriceFromTickString(data, 45, "1694438400")).toBe(false);
    expect(data.volume).toBeNull();
  });
});
