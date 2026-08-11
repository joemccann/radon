import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { buildSyntheticComboDepth, type SyntheticComboLeg } from "@/lib/book/syntheticComboDepth";
import type { DepthBook, DepthLevel } from "@/lib/pricesProtocol";

type CatalogLeg = { type: string; action: "BUY" | "SELL"; ratio?: number };
type CatalogStructure = { name: string; legs: CatalogLeg[] };

const catalogPath = resolve(__dirname, "../../docs/options-structures.json");
const catalog = (JSON.parse(readFileSync(catalogPath, "utf8")) as CatalogStructure[])
  .filter(({ legs }) => legs.length >= 2);

const level = (price: number, size: number, exchange: string): DepthLevel => ({
  price, size, exchange, marketMaker: null,
});

function catalogBook(structure: CatalogStructure, leg: CatalogLeg, index: number): DepthBook {
  const price = 10 + index;
  return {
    symbol: `${structure.name}:${index}`,
    kind: leg.type.toLowerCase() === "stock" ? "stock" : "option",
    bid: [level(price, 100, `B${index}`)],
    ask: [level(price + 0.1, 100, `A${index}`)],
    isSmartDepth: true,
    feed: "TEST BBO",
    entitled: true,
    timestamp: "2026-08-11T16:00:00.000Z",
  };
}

describe("synthetic combo depth options-structure catalog", () => {
  it.each(catalog)("builds executable signed depth for $name", (structure) => {
    const legs: SyntheticComboLeg[] = structure.legs.map((leg, index) => ({
      direction: leg.action === "BUY" ? "LONG" : "SHORT",
      contracts: leg.ratio ?? 1,
      book: catalogBook(structure, leg, index),
    }));
    const result = buildSyntheticComboDepth(structure.name, legs);
    const expectedBid = structure.legs.reduce((total, leg, index) => {
      const ratio = leg.ratio ?? 1;
      return total + (leg.action === "BUY" ? ratio * (10 + index) : -ratio * (10.1 + index));
    }, 0);
    const expectedAsk = structure.legs.reduce((total, leg, index) => {
      const ratio = leg.ratio ?? 1;
      return total + (leg.action === "BUY" ? ratio * (10.1 + index) : -ratio * (10 + index));
    }, 0);

    expect(result, structure.name).not.toBeNull();
    expect(result?.nbbo?.bestBid).toBeCloseTo(expectedBid, 8);
    expect(result?.nbbo?.bestAsk).toBeCloseTo(expectedAsk, 8);
    expect(result?.nbbo?.bestBid).toBeLessThanOrEqual(result?.nbbo?.bestAsk ?? -Infinity);
  });

  it("covers every multi-leg catalog entry, including stock-mixed and mixed-expiry structures", () => {
    expect(catalog).toHaveLength(52);
    expect(catalog.filter(({ legs }) => legs.some(({ type }) => type.toLowerCase() === "stock")).map(({ name }) => name))
      .toEqual(expect.arrayContaining(["Covered Call", "Collar (Zero-Cost Collar)", "Reverse Conversion"]));
    expect(catalog.map(({ name }) => name)).toEqual(expect.arrayContaining([
      "Diagonal Call Spread", "Calendar Put Spread (Same Strike)", "Double Calendar Spread",
    ]));
  });
});
