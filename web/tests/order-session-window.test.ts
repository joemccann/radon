import { describe, expect, it } from "vitest";
import type { OpenOrder } from "../lib/types";
import type { OpenOrderDisplayRow } from "../lib/openOrderCombos";
import { SECTION_TOOLTIPS } from "../lib/sectionTooltips";
import { MarketState } from "../lib/useMarketHours";
import {
  classifyDisplayRowSession,
  classifyOrderSession,
} from "../lib/orders/sessionWindow";

const OPEN_NOW = new Date("2026-07-09T15:00:00.000Z"); // 11:00 ET Thursday, RTH
const EXTENDED_NOW = new Date("2026-08-27T21:30:00.000Z"); // 17:30 ET Thursday, extended
const CLOSED_NOW = new Date("2026-08-28T01:00:00.000Z"); // 21:00 ET Thursday, closed

function makeOrder(overrides: Partial<OpenOrder> = {}): OpenOrder {
  const totalQuantity = overrides.totalQuantity ?? 10;
  return {
    orderId: overrides.orderId ?? 1,
    permId: overrides.permId ?? 1001,
    symbol: overrides.symbol ?? "AAPL",
    contract: overrides.contract ?? {
      conId: 1,
      symbol: "AAPL",
      secType: "STK",
      strike: null,
      right: null,
      expiry: null,
    },
    action: overrides.action ?? "BUY",
    orderType: overrides.orderType ?? "LMT",
    totalQuantity,
    limitPrice: overrides.limitPrice ?? 100,
    auxPrice: overrides.auxPrice ?? null,
    status: overrides.status ?? "Submitted",
    filled: overrides.filled ?? 0,
    remaining: overrides.remaining ?? totalQuantity,
    avgFillPrice: overrides.avgFillPrice ?? null,
    tif: overrides.tif ?? "DAY",
    ...overrides,
  };
}

function opt(overrides: Partial<OpenOrder> = {}): OpenOrder {
  return makeOrder({
    symbol: "AAPL",
    contract: {
      conId: 10,
      symbol: "AAPL",
      secType: "OPT",
      strike: 200,
      right: "C",
      expiry: "2026-08-21",
    },
    ...overrides,
  });
}

function singleRow(order: OpenOrder): OpenOrderDisplayRow {
  return { kind: "single", index: 0, summary: null, order };
}

function comboRow(orders: OpenOrder[]): OpenOrderDisplayRow {
  return {
    kind: "combo",
    id: "combo-AAPL-1",
    index: 0,
    symbol: "AAPL",
    structure: "Call Spread",
    summary: "Call Spread (Long Call 200 / Short Call 210)",
    orders,
    totalQuantity: orders[0]?.totalQuantity ?? 1,
    orderType: "BAG",
    status: orders[0]?.status ?? "Submitted",
    tif: orders[0]?.tif ?? "DAY",
    limitPrice: orders[0]?.limitPrice ?? null,
  };
}

function expectNoEmDash(hint: string) {
  expect(hint).not.toMatch(/\u2014/);
  expect(hint).toMatch(/after 16:00 ET/);
}

describe("classifyOrderSession eligibility", () => {
  it("classifies OPT DAY as rth-only", () => {
    const session = classifyOrderSession(opt({ tif: "DAY" }), OPEN_NOW);
    expect(session.eligibility).toBe("rth-only");
    expect(session.label).toBe("RTH");
    expect(session.tone).toBe("rth");
    expect(session.hint).toBe("Will not fill after 16:00 ET.");
    expectNoEmDash(session.hint);
  });

  it("classifies OPT GTC as rth-only even when outsideRth is true", () => {
    const session = classifyOrderSession(
      opt({ tif: "GTC", outsideRth: true }),
      OPEN_NOW,
    );
    expect(session.eligibility).toBe("rth-only");
    expect(session.label).toBe("RTH");
    expect(session.hint).toBe("Will not fill after 16:00 ET.");
  });

  it("classifies STK GTC with outsideRth false as rth-only", () => {
    const session = classifyOrderSession(
      makeOrder({ tif: "GTC", outsideRth: false, symbol: "TQQQ" }),
      OPEN_NOW,
    );
    expect(session.eligibility).toBe("rth-only");
  });

  it("classifies STK GTC with outsideRth true as extended", () => {
    const session = classifyOrderSession(
      makeOrder({
        tif: "GTC",
        outsideRth: true,
        symbol: "TQQQ",
        contract: {
          conId: 2,
          symbol: "TQQQ",
          secType: "STK",
          strike: null,
          right: null,
          expiry: null,
        },
      }),
      OPEN_NOW,
    );
    expect(session.eligibility).toBe("extended");
    expect(session.label).toBe("EXT");
    expect(session.tone).toBe("extended");
    expect(session.hint).toBe("Can fill after 16:00 ET.");
    expectNoEmDash(session.hint);
  });

  it("classifies STK DAY with outsideRth true as extended", () => {
    const session = classifyOrderSession(
      makeOrder({ tif: "DAY", outsideRth: true, symbol: "TQQQ" }),
      OPEN_NOW,
    );
    expect(session.eligibility).toBe("extended");
  });

  it("classifies BAG option combo as rth-only even with outsideRth true", () => {
    const session = classifyOrderSession(
      makeOrder({
        tif: "GTC",
        outsideRth: true,
        contract: {
          conId: 9,
          symbol: "AAPL",
          secType: "BAG",
          strike: null,
          right: null,
          expiry: null,
        },
      }),
      OPEN_NOW,
    );
    expect(session.eligibility).toBe("rth-only");
    expect(session.hint).toBe("Will not fill after 16:00 ET.");
  });

  it("treats missing outsideRth as false (IB default)", () => {
    const order = makeOrder({ tif: "GTC" });
    expect(order.outsideRth).toBeUndefined();
    const session = classifyOrderSession(order, OPEN_NOW);
    expect(session.eligibility).toBe("rth-only");
  });

  it("classifies FUT as extended even without outsideRth", () => {
    const session = classifyOrderSession(
      makeOrder({
        tif: "GTC",
        contract: {
          conId: 3,
          symbol: "ES",
          secType: "FUT",
          strike: null,
          right: null,
          expiry: "202609",
        },
      }),
      OPEN_NOW,
    );
    expect(session.eligibility).toBe("extended");
  });

  it("classifies FOP as extended even without outsideRth", () => {
    const session = classifyOrderSession(
      makeOrder({
        tif: "GTC",
        contract: {
          conId: 4,
          symbol: "ES",
          secType: "FOP",
          strike: 5000,
          right: "C",
          expiry: "202609",
        },
      }),
      OPEN_NOW,
    );
    expect(session.eligibility).toBe("extended");
    expect(session.hint).toBe("Can fill after 16:00 ET.");
  });
});

describe("classifyOrderSession labels at frozen 17:30 ET", () => {
  it("maps GTC rth-only to NEXT RTH during extended hours", () => {
    const session = classifyOrderSession(opt({ tif: "GTC" }), EXTENDED_NOW);
    expect(session.marketState).toBe(MarketState.EXTENDED);
    expect(session.eligibility).toBe("rth-only");
    expect(session.label).toBe("NEXT RTH");
    expect(session.tone).toBe("rth");
  });

  it("maps STK extended to EXT LIVE during extended hours", () => {
    const session = classifyOrderSession(
      makeOrder({ tif: "GTC", outsideRth: true, symbol: "TQQQ" }),
      EXTENDED_NOW,
    );
    expect(session.marketState).toBe(MarketState.EXTENDED);
    expect(session.eligibility).toBe("extended");
    expect(session.label).toBe("EXT LIVE");
    expect(session.tone).toBe("extended");
  });

  it("maps DAY rth-only to EXPIRES after the close", () => {
    const session = classifyOrderSession(opt({ tif: "DAY" }), EXTENDED_NOW);
    expect(session.eligibility).toBe("rth-only");
    expect(session.label).toBe("EXPIRES");
    expect(session.tone).toBe("expires");
    expect(session.hint).toBe("Will not fill after 16:00 ET.");
    expectNoEmDash(session.hint);
  });
});

describe("classifyOrderSession labels when the cash session is closed", () => {
  it("maps DAY rth-only to EXPIRES after 20:00 ET", () => {
    const session = classifyOrderSession(opt({ tif: "DAY" }), CLOSED_NOW);
    expect(session.marketState).toBe(MarketState.CLOSED);
    expect(session.label).toBe("EXPIRES");
    expect(session.tone).toBe("expires");
  });

  it("maps GTC rth-only to NEXT RTH after 20:00 ET", () => {
    const session = classifyOrderSession(opt({ tif: "GTC" }), CLOSED_NOW);
    expect(session.label).toBe("NEXT RTH");
    expect(session.tone).toBe("rth");
  });

  it("keeps extended stocks on EXT when the cash session is closed", () => {
    const session = classifyOrderSession(
      makeOrder({ tif: "GTC", outsideRth: true, symbol: "TQQQ" }),
      CLOSED_NOW,
    );
    expect(session.eligibility).toBe("extended");
    expect(session.label).toBe("EXT");
    expect(session.tone).toBe("extended");
  });
});

describe("Open Orders tooltip", () => {
  it("names RTH vs EXT fill windows without an em dash", () => {
    const copy = SECTION_TOOLTIPS["Open Orders"];
    expect(copy).toMatch(/RTH vs EXT/);
    expect(copy).toMatch(/after 16:00 ET/);
    expect(copy).not.toMatch(/\u2014/);
  });
});

describe("classifyDisplayRowSession", () => {
  it("classifies a single STK row from the order", () => {
    const row = singleRow(makeOrder({ tif: "GTC", outsideRth: true, symbol: "TQQQ" }));
    const session = classifyDisplayRowSession(row, OPEN_NOW);
    expect(session.eligibility).toBe("extended");
    expect(session.label).toBe("EXT");
  });

  it("classifies a grouped option combo row as rth-only even with outsideRth", () => {
    const legs = [
      opt({ permId: 10, tif: "GTC", outsideRth: true }),
      opt({
        permId: 11,
        orderId: 2,
        action: "SELL",
        tif: "GTC",
        outsideRth: true,
        contract: {
          conId: 11,
          symbol: "AAPL",
          secType: "OPT",
          strike: 210,
          right: "C",
          expiry: "2026-08-21",
        },
      }),
    ];
    const session = classifyDisplayRowSession(comboRow(legs), OPEN_NOW);
    expect(session.eligibility).toBe("rth-only");
    expect(session.label).toBe("RTH");
    expect(session.hint).toBe("Will not fill after 16:00 ET.");
  });
});
