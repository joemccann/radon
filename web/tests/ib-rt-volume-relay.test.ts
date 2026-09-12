/**
 * Relay wiring for option session volume.
 *
 * Generic tick 233 (RT Volume) is requested on every live reqMktData, but IB
 * delivers it as tickString type 48 — not tickPrice / tickSize. Volume also
 * arrives as tickSize type 8. Both paths must land in PriceData and be
 * broadcast; otherwise a thin option whose book is already quoted (bid/ask
 * tickPrice) keeps volume=null forever and the order sheet renders VOLUME ---.
 *
 * HIGH/LOW stay tickPrice 6/7 (delayed 72/73). This file does not invent them.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = resolve(fileURLToPath(import.meta.url), "..");
const projectRoot = resolve(__dirname, "..", "..");
const source = readFileSync(resolve(projectRoot, "scripts", "ib_realtime_server.js"), "utf8");
const handlerPath = new URL("../../scripts/ib_tick_handler.js", import.meta.url).pathname;
const {
  createPriceData,
  parseRtVolume,
  updatePriceFromTickString,
} = await import(handlerPath);

const RT_VOLUME = 48;

describe("ib_realtime_server.js — RT_VOLUME + tickSize broadcast", () => {
  it("still requests generic ticks 233 and 165 on live subscriptions", () => {
    expect(source).toContain('ib.reqMktData(nextTickerId, ibContract, "233,165", false, false)');
  });

  it("registers EventName.tickString and applies updatePriceFromTickString", () => {
    expect(source).toContain("EventName.tickString");
    expect(source).toContain("updatePriceFromTickString");
    expect(source).toMatch(/ib\.on\(\s*EventName\.tickString/);
  });

  it("broadcasts after a live tickSize update so volume is not stuck until the next price tick", () => {
    const fn = source.match(/function onTickSize\([\s\S]*?\n\}/)?.[0] ?? "";
    expect(fn).toContain("updatePriceFromTickSize");
    expect(fn).toContain("hydrateAndBroadcast(symbol)");
  });

  it("broadcasts after a live RT_VOLUME tickString update", () => {
    const fn = source.match(/function onTickString\([\s\S]*?\n\}/)?.[0] ?? "";
    expect(fn).toContain("updatePriceFromTickString");
    expect(fn).toContain("hydrateAndBroadcast(symbol)");
  });
});

describe("parseRtVolume — SNDK-shaped option payload", () => {
  it("reads contract volume from the 4th semicolon field", () => {
    expect(parseRtVolume("4.20;1;1694438400;17;4.18;true")).toBe(17);
    expect(updatePriceFromTickString(
      createPriceData("SNDK_20260925_1750_C"),
      RT_VOLUME,
      "4.20;1;1694438400;17;4.18;true",
    )).toBe(true);
  });
});
