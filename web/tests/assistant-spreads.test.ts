import { describe, expect, it } from "vitest";
import { midOf, rankVerticalSpreads, type ChainContract } from "@/lib/assistant/spreads";

function call(strike: number, bid: number, ask: number): ChainContract {
  return { strike, right: "C", bid, ask, mid: (bid + ask) / 2 };
}

describe("assistant spread ranking", () => {
  const chain: ChainContract[] = [
    call(470, 18.0, 18.4),
    call(480, 10.0, 10.4),
    call(490, 5.8, 6.2),
    call(500, 4.0, 4.4),
    call(520, 1.0, 1.2),
  ];

  it("computes mid from bid/ask when mid is missing", () => {
    expect(midOf({ strike: 100, right: "C", bid: 2, ask: 4, mid: null })).toBe(3);
    expect(midOf({ strike: 100, right: "C", bid: null, ask: null, mid: 1.5 })).toBe(1.5);
    expect(midOf({ strike: 100, right: "C", bid: 0, ask: 0, mid: null })).toBeNull();
  });

  it("ranks bull call spreads by max payout dollars and flags convexity", () => {
    const ranked = rankVerticalSpreads({
      spot: 480,
      contracts: chain,
      kind: "bull_call",
      quantity: 10,
    });

    expect(ranked.length).toBeGreaterThan(0);
    const tight = ranked.find((row) => row.buyStrike === 480 && row.sellStrike === 500);
    expect(tight).toBeDefined();
    // debit = 10.2 - 4.2 = 6.0; width = 20; max profit = 14.0 / contract (convex)
    expect(tight!.debit).toBeCloseTo(6.0, 4);
    expect(tight!.width).toBe(20);
    expect(tight!.maxProfit).toBeCloseTo(14.0, 4);
    expect(tight!.maxPayoutDollars).toBeCloseTo(14_000, 0);
    expect(tight!.rewardToRisk).toBeCloseTo(14 / 6, 4);
    expect(tight!.convex).toBe(true);

    const convex = ranked.filter((row) => row.convex);
    expect(ranked.slice(0, convex.length).every((row) => row.convex)).toBe(true);
    const convexPayouts = convex.map((row) => row.maxPayoutDollars);
    expect(convexPayouts).toEqual([...convexPayouts].sort((a, b) => b - a));
  });

  it("skips pairs without a positive debit or a quoted mid", () => {
    const ranked = rankVerticalSpreads({
      spot: 480,
      contracts: [
        call(480, 1, 1.2),
        { strike: 500, right: "C", bid: null, ask: null, mid: null },
      ],
      kind: "bull_call",
      quantity: 1,
    });
    expect(ranked).toEqual([]);
  });
});
