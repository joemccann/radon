import { describe, expect, it } from "vitest";
import type { OpenOrder } from "../lib/types";
import type { OpenOrderDisplayRow } from "../lib/openOrderCombos";
import { SECTION_TOOLTIPS } from "../lib/sectionTooltips";
import { MarketState } from "../lib/useMarketHours";
import {
  classifyDisplayRowSession,
  classifyOrderSession,
  isExtendedFillLive,
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

  // R-338: these two asserted the defect. `outsideRth: false` is what
  // `/api/orders/place` actually transmits for a futures order entered during
  // RTH (`body.outsideRth ?? getMarketStateFromDate() !== "open"`), so
  // "extended even without outsideRth" made the chip promise an after-hours
  // fill for an order that is inert until the next morning. The STK rule
  // beside it always required the flag. Both cases keep their original shape
  // and now assert the eligibility on BOTH sides of the flag.
  it("classifies FUT as extended only when the order opted in", () => {
    const contract = {
      conId: 3, symbol: "ES", secType: "FUT",
      strike: null, right: null, expiry: "202609",
    };
    expect(
      classifyOrderSession(makeOrder({ tif: "GTC", contract }), OPEN_NOW).eligibility,
    ).toBe("rth-only");
    expect(
      classifyOrderSession(
        makeOrder({ tif: "GTC", contract, outsideRth: true }), OPEN_NOW,
      ).eligibility,
    ).toBe("extended");
  });

  it("classifies FOP as extended only when the order opted in", () => {
    const contract = {
      conId: 4, symbol: "ES", secType: "FOP",
      strike: 5000, right: "C", expiry: "202609",
    };
    expect(
      classifyOrderSession(makeOrder({ tif: "GTC", contract }), OPEN_NOW).eligibility,
    ).toBe("rth-only");
    const optedIn = classifyOrderSession(
      makeOrder({ tif: "GTC", contract, outsideRth: true }), OPEN_NOW,
    );
    expect(optedIn.eligibility).toBe("extended");
    expect(optedIn.hint).toBe("Can fill after 16:00 ET.");
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

describe("isExtendedFillLive", () => {
  const extStock = () => makeOrder({ tif: "DAY", outsideRth: true, symbol: "TQQQ" });

  it("is true for an extended-eligible stock during a live extended session", () => {
    expect(isExtendedFillLive(classifyOrderSession(extStock(), EXTENDED_NOW))).toBe(true);
  });

  it("is false for the same order once the extended session has closed", () => {
    expect(isExtendedFillLive(classifyOrderSession(extStock(), CLOSED_NOW))).toBe(false);
  });

  it("is false during RTH (the regular session is the fill window, not EXT)", () => {
    expect(isExtendedFillLive(classifyOrderSession(extStock(), OPEN_NOW))).toBe(false);
  });

  it("is false for an rth-only option even during extended hours", () => {
    expect(isExtendedFillLive(classifyOrderSession(opt({ tif: "GTC" }), EXTENDED_NOW))).toBe(false);
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

/* ── R-336 / R-337 / R-338 / R-367 / REL-121 ───────────────────────────────
 *
 * The chip makes a definitive fill claim off `marketStateAt`, which models
 * neither US holidays nor early closes. Four independent ways it was wrong:
 *
 *  R-336 at 14:30 ET on a weekday holiday or an early-close day it read RTH,
 *        every resting DAY order was presented as still fillable, and the
 *        EXPIRES warning that would otherwise fire post-close was suppressed.
 *  R-337 a grouped combo carries `tif: "MIXED"` when its legs disagree, which
 *        fell through to the GTC branch and was labelled NEXT RTH — "survives
 *        to the next session". In fact the DAY leg is cancelled at the close
 *        and the GTC leg rests alone as a naked short.
 *  R-338 FUT/FOP returned "extended" unconditionally, ignoring the order's own
 *        `outsideRth`, where the STK rule correctly requires it. A futures
 *        order entered at 11:00 ET is transmitted with `outsideRth: false` and
 *        still read EXT LIVE at 18:00 ET.
 *  R-367 the extended branch returned BEFORE the TIF check, so an
 *        extended-eligible DAY order could never be labelled EXPIRES — the
 *        operator got an expiry warning or not based on a flag unrelated to
 *        expiry.
 */

const HOLIDAY_1430_ET = new Date("2026-11-26T19:30:00.000Z"); // Thanksgiving
const EARLY_CLOSE_1430_ET = new Date("2026-11-27T19:30:00.000Z"); // 13:00 ET close
const EARLY_CLOSE_1200_ET = new Date("2026-11-27T17:00:00.000Z"); // before the 13:00 close

function fut(overrides: Partial<OpenOrder> = {}): OpenOrder {
  return makeOrder({
    symbol: "ES",
    contract: {
      conId: 99, symbol: "ES", secType: "FUT",
      strike: null, right: null, expiry: "2026-12-18",
    },
    ...overrides,
  });
}

describe("session chip — calendar awareness (R-336)", () => {
  it("does not claim RTH at 14:30 ET on a weekday holiday", () => {
    const session = classifyOrderSession(makeOrder({ tif: "DAY" }), HOLIDAY_1430_ET);
    expect(session.label).not.toBe("RTH");
    expect(session.marketState).not.toBe(MarketState.OPEN);
  });

  it("does not claim RTH at 14:30 ET on an early-close day", () => {
    const session = classifyOrderSession(makeOrder({ tif: "DAY" }), EARLY_CLOSE_1430_ET);
    expect(session.label).not.toBe("RTH");
  });

  it("still claims RTH before the early close on that same day", () => {
    const session = classifyOrderSession(makeOrder({ tif: "DAY" }), EARLY_CLOSE_1200_ET);
    expect(session.label).toBe("RTH");
  });

  it("warns that a DAY order expires once the holiday session is over", () => {
    const session = classifyOrderSession(makeOrder({ tif: "DAY" }), HOLIDAY_1430_ET);
    expect(session.label).toBe("EXPIRES");
    expect(session.tone).toBe("expires");
  });

  it("leaves an ordinary full session exactly as before", () => {
    expect(classifyOrderSession(makeOrder({ tif: "DAY" }), OPEN_NOW).label).toBe("RTH");
  });
});

describe("session chip — MIXED combo tif (R-337)", () => {
  it("does not label a MIXED combo containing a DAY leg as NEXT RTH", () => {
    const session = classifyOrderSession(
      { tif: "MIXED", contract: { secType: "BAG" } },
      CLOSED_NOW,
    );
    expect(session.label).not.toBe("NEXT RTH");
    expect(session.label).toBe("EXPIRES");
    expect(session.tone).toBe("expires");
  });

  it("a genuinely all-GTC combo still reads NEXT RTH", () => {
    const session = classifyOrderSession(
      { tif: "GTC", contract: { secType: "BAG" } },
      CLOSED_NOW,
    );
    expect(session.label).toBe("NEXT RTH");
  });

  it("drives the same verdict through the display-row path", () => {
    const row = comboRow([
      opt({ orderId: 1, tif: "DAY" }),
      opt({ orderId: 2, tif: "GTC" }),
    ]);
    row.tif = "MIXED";
    expect(classifyDisplayRowSession(row, CLOSED_NOW).label).toBe("EXPIRES");
  });
});

describe("session chip — futures need outsideRth (R-338)", () => {
  it("does not render EXT LIVE for a futures order with outsideRth false", () => {
    const session = classifyOrderSession(
      fut({ tif: "GTC", outsideRth: false }),
      EXTENDED_NOW,
    );
    expect(session.label).not.toBe("EXT LIVE");
    expect(session.eligibility).toBe("rth-only");
  });

  it("still renders EXT LIVE for a futures order that opted in", () => {
    const session = classifyOrderSession(
      fut({ tif: "GTC", outsideRth: true }),
      EXTENDED_NOW,
    );
    expect(session.label).toBe("EXT LIVE");
    expect(session.eligibility).toBe("extended");
  });
});

describe("session chip — DAY expiry on the extended branch (R-367)", () => {
  it("warns a DAY order with outsideRth true that IB cancels it at 20:00 ET", () => {
    const session = classifyOrderSession(
      makeOrder({ tif: "DAY", outsideRth: true }),
      EXTENDED_NOW,
    );
    expect(session.tone).toBe("expires");
    expect(session.hint).toMatch(/20:00 ET/);
  });

  it("a GTC extended order keeps the plain EXT LIVE chip", () => {
    const session = classifyOrderSession(
      makeOrder({ tif: "GTC", outsideRth: true }),
      EXTENDED_NOW,
    );
    expect(session.label).toBe("EXT LIVE");
    expect(session.tone).toBe("extended");
  });
});
