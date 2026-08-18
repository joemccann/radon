/**
 * @vitest-environment jsdom
 *
 * A combo's Max loss / Max gain must scale with the entered contract count.
 *
 * Bug (2026-08-18 screenshot): a 150-lot SPCX bull call spread showed
 * "You'll pay $26,700.00" (correctly ×150) next to "Max loss $187.00 /
 * Max gain $813.00" — one spread's risk, not one hundred and fifty.
 *
 * Root cause: `MobileOrderTicket` built its `OrderRiskInput.chainLegs` from
 * `normalizeComboOrder(...).legs`, whose quantities are divided by their GCD
 * (150/150 -> 1/1). The risk layer derives `comboQuantity` as the GCD of the
 * quantities it is HANDED — the documented contract is raw user-entered
 * counts ("never pre-normalize", `computeOrderRisk.ts` ChainOrderLeg docs) —
 * so pre-divided legs made every multi-lot combo price its risk as one lot.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import MobileOrderTicket from "../components/mobile/MobileOrderTicket";
import type { OrderLeg } from "@/lib/optionsChainUtils";
import type { PortfolioData } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

vi.mock("@/components/ComboSkewPanel", () => ({ default: () => null }));

const TICKER = "AAPL";
const EXPIRY = "20260320";

function makePrice(symbol: string, bid: number, ask: number): PriceData {
  return {
    symbol,
    last: (bid + ask) / 2,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 1,
    askSize: 1,
    volume: 100,
    high: null,
    low: null,
    open: null,
    close: (bid + ask) / 2,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: new Date().toISOString(),
  };
}

// Both legs quoted so the combo resolves as a net DEBIT (buy 200C rich,
// sell 210C cheap) and the teaser renders.
const PRICES: Record<string, PriceData> = {
  [`${TICKER}_${EXPIRY}_200_C`]: makePrice(`${TICKER}_${EXPIRY}_200_C`, 3.0, 3.4),
  [`${TICKER}_${EXPIRY}_210_C`]: makePrice(`${TICKER}_${EXPIRY}_210_C`, 1.2, 1.4),
};

const EMPTY_PORTFOLIO = { positions: [] } as unknown as PortfolioData;

function spreadLegs(quantity: number): OrderLeg[] {
  return [
    {
      id: `${TICKER}_${EXPIRY}_200_C`,
      action: "BUY",
      right: "C",
      strike: 200,
      expiry: EXPIRY,
      quantity,
      limitPrice: null,
    },
    {
      id: `${TICKER}_${EXPIRY}_210_C`,
      action: "SELL",
      right: "C",
      strike: 210,
      expiry: EXPIRY,
      quantity,
      limitPrice: null,
    },
  ];
}

function renderSpread(quantity: number) {
  return render(
    <MobileOrderTicket
      open
      ticker={TICKER}
      legs={spreadLegs(quantity)}
      prices={PRICES}
      spot={210}
      portfolio={EMPTY_PORTFOLIO}
      onClose={() => {}}
      onRemoveLeg={() => {}}
      onUpdateLeg={() => {}}
      onClearLegs={() => {}}
    />,
  );
}

/** Parse "Max loss $26,700.00 / Max gain $123,300.00" into numbers. */
function parseTeaser(text: string): { maxLoss: number; maxGain: number } {
  const match = text.match(/Max loss \$([\d,.]+) \/ Max gain \$([\d,.]+)/);
  expect(match, `teaser did not carry bounded risk numbers: "${text}"`).not.toBeNull();
  return {
    maxLoss: Number(match![1].replace(/,/g, "")),
    maxGain: Number(match![2].replace(/,/g, "")),
  };
}

function teaserRiskFor(quantity: number): { maxLoss: number; maxGain: number } {
  const { getByTestId, unmount } = renderSpread(quantity);
  fireEvent.change(getByTestId("mobile-order-ticket-price-input"), {
    target: { value: "1.78" },
  });
  const risk = parseTeaser(
    getByTestId("mobile-order-ticket-teaser").textContent ?? "",
  );
  unmount();
  return risk;
}

beforeEach(() => {
  global.fetch = vi.fn(() =>
    Promise.resolve({ ok: true, json: async () => ({}) }),
  ) as unknown as typeof fetch;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("MobileOrderTicket — combo risk scales with contract count", () => {
  it("prices a 150-lot debit spread's max loss/gain at 150x the 1-lot", () => {
    const oneLot = teaserRiskFor(1);
    const bigLot = teaserRiskFor(150);

    // Ratio, not absolute dollars: per-contract cost adjustments may shift
    // both sides a little, but a 150-lot can never price as one spread.
    expect(bigLot.maxLoss / oneLot.maxLoss).toBeGreaterThan(100);
    expect(bigLot.maxGain / oneLot.maxGain).toBeGreaterThan(100);
  });

  it("keeps max loss consistent with the You'll-pay notional on a debit spread", () => {
    const { getByTestId } = renderSpread(150);
    fireEvent.change(getByTestId("mobile-order-ticket-price-input"), {
      target: { value: "1.78" },
    });
    const text = getByTestId("mobile-order-ticket-teaser").textContent ?? "";

    // The screenshot's contradiction: pay $26,700 but "max loss $187".
    // A debit spread's max loss is the debit (plus costs) — same order of
    // magnitude as the notional, never two orders below it.
    const { maxLoss } = parseTeaser(text);
    expect(text).toContain("26,700");
    expect(maxLoss).toBeGreaterThanOrEqual(26_700);
  });
});
