import { describe, expect, it } from "vitest";

/**
 * Pure realized-P&L lot matcher for the assistant journal tools
 * (`@/lib/journal/realizedPnl`).
 *
 * Ground truth this pins (2026-07-24 weekly-P&L incident):
 *   - The journal carries TWO row families for the same fills: Flex-rehydrate
 *     aggregates (composite ib_exec_id, basis fields) and realtime per-fill
 *     rows. Dedup is mandatory or totals double-count.
 *   - Closes lot-match against opens OUTSIDE the queried window (SNDK 4-of-5
 *     cover on 07-21 against a 5-lot SELL_TO_OPEN on 07-16).
 *   - Realized P&L is attributed to the CLOSE date, net of commissions.
 *
 * Every expected value is DERIVED in this file from the fixtures' own
 * primitive arithmetic; the incident totals (+30,689.16 SNDK, +7,220.79 EWY,
 * +37,909.95 combined) are pinned alongside as targets.
 */

import {
  computeRealizedPnl,
  dedupJournalRows,
  classifyRowFamily,
  type RealizedJournalRow,
} from "@/lib/journal/realizedPnl";

const WINDOW = { from: "2026-07-19", to: "2026-07-24" };

function row(
  tradeId: string,
  filledAt: string,
  payload: Record<string, unknown>,
): RealizedJournalRow {
  return {
    trade_id: tradeId,
    filled_at: filledAt,
    payload: { ...payload, filled_at: filledAt },
  } as RealizedJournalRow;
}

/* ── SNDK: short 5x $1500P opened 07-16, 4 covered 07-21 ─────────────────── */

const SNDK = { ticker: "SNDK", strike: 1500, right: "P", expiry: "20260821", structure: "Short Put" };
const SNDK_OPEN_PRICE = 139.55;
const SNDK_OPEN_COMMISSIONS = [2.0, 3.0] as const; // per fill (2x, 3x)
const SNDK_OPEN_COMMISSION_TOTAL = SNDK_OPEN_COMMISSIONS[0] + SNDK_OPEN_COMMISSIONS[1]; // 5.00
const SNDK_CLOSE_PRICE = 62.8;
const SNDK_CLOSE_COMMISSIONS = [1.71, 5.13] as const; // per fill (1x, 3x)
const SNDK_CLOSE_COMMISSION_TOTAL = SNDK_CLOSE_COMMISSIONS[0] + SNDK_CLOSE_COMMISSIONS[1]; // 6.84

const sndkOpenNetCredit = 5 * SNDK_OPEN_PRICE * 100 - SNDK_OPEN_COMMISSION_TOTAL;
const sndkOpenNetCreditPerContract = sndkOpenNetCredit / 5;
const SNDK_EXPECTED =
  4 * sndkOpenNetCreditPerContract - (4 * SNDK_CLOSE_PRICE * 100 + SNDK_CLOSE_COMMISSION_TOTAL);

const sndkFillOpen1 = row("sndk-fo1", "2026-07-16T14:31:00Z", {
  ...SNDK, action: "SELL_TO_OPEN", contracts: 2, fill_price: SNDK_OPEN_PRICE,
  commission: SNDK_OPEN_COMMISSIONS[0], ib_exec_id: "sndko1",
});
const sndkFillOpen2 = row("sndk-fo2", "2026-07-16T14:32:00Z", {
  ...SNDK, action: "SELL_TO_OPEN", contracts: 3, fill_price: SNDK_OPEN_PRICE,
  commission: SNDK_OPEN_COMMISSIONS[1], ib_exec_id: "sndko2",
});
const sndkFlexOpen = row("sndk-xo", "2026-07-16T14:32:00Z", {
  ...SNDK, action: "SELL_TO_OPEN", contracts: 5, fill_price: SNDK_OPEN_PRICE,
  commission: SNDK_OPEN_COMMISSION_TOTAL, ib_exec_id: "sndko1+sndko2",
  open_basis: sndkOpenNetCredit,
});
const sndkFillClose1 = row("sndk-fc1", "2026-07-21T15:02:00Z", {
  ...SNDK, action: "BUY_TO_CLOSE", contracts: 1, fill_price: SNDK_CLOSE_PRICE,
  commission: SNDK_CLOSE_COMMISSIONS[0], ib_exec_id: "sndkc1",
});
const sndkFillClose2 = row("sndk-fc2", "2026-07-21T15:02:30Z", {
  ...SNDK, action: "BUY_TO_CLOSE", contracts: 3, fill_price: SNDK_CLOSE_PRICE,
  commission: SNDK_CLOSE_COMMISSIONS[1], ib_exec_id: "sndkc2",
});
const sndkFlexClose = row("sndk-xc", "2026-07-21T15:02:30Z", {
  ...SNDK, action: "BUY_TO_CLOSE", contracts: 4, fill_price: SNDK_CLOSE_PRICE,
  commission: SNDK_CLOSE_COMMISSION_TOTAL, ib_exec_id: "sndkc1+sndkc2",
  realized_pnl: SNDK_EXPECTED, cost_basis: 4 * SNDK_CLOSE_PRICE * 100 + SNDK_CLOSE_COMMISSION_TOTAL,
  proceeds: 4 * sndkOpenNetCreditPerContract, open_basis: sndkOpenNetCredit, realized_quantity: 4,
});

const SNDK_ALL = [sndkFillOpen1, sndkFillOpen2, sndkFlexOpen, sndkFillClose1, sndkFillClose2, sndkFlexClose];
const SNDK_FILLS_ONLY = [sndkFillOpen1, sndkFillOpen2, sndkFillClose1, sndkFillClose2];
const SNDK_FLEX_ONLY = [sndkFlexOpen, sndkFlexClose];

/* ── EWY: short 25x $175C opened 07-21, covered 07-23 (fill-family close ONLY:
       rehydrate lag means no realized_pnl anywhere — raw-fill arithmetic) ── */

const EWY = { ticker: "EWY", strike: 175, right: "C", expiry: "20260918", structure: "Short Call" };
const EWY_OPEN_PRICE = 4.55;
const EWY_OPEN_COMMISSIONS = [5.84, 8.76] as const; // per fill (10x, 15x)
const EWY_OPEN_COMMISSION_TOTAL = EWY_OPEN_COMMISSIONS[0] + EWY_OPEN_COMMISSIONS[1]; // 14.60
const EWY_CLOSE_PRICE = 1.65;
const EWY_CLOSE_COMMISSION = 14.61;

const EWY_EXPECTED =
  (25 * EWY_OPEN_PRICE * 100 - EWY_OPEN_COMMISSION_TOTAL)
  - (25 * EWY_CLOSE_PRICE * 100 + EWY_CLOSE_COMMISSION);

const ewyFillOpen1 = row("ewy-fo1", "2026-07-21T13:45:00Z", {
  ...EWY, action: "SELL_TO_OPEN", contracts: 10, fill_price: EWY_OPEN_PRICE,
  commission: EWY_OPEN_COMMISSIONS[0], ib_exec_id: "ewyo1",
});
const ewyFillOpen2 = row("ewy-fo2", "2026-07-21T13:46:00Z", {
  ...EWY, action: "SELL_TO_OPEN", contracts: 15, fill_price: EWY_OPEN_PRICE,
  commission: EWY_OPEN_COMMISSIONS[1], ib_exec_id: "ewyo2",
});
const ewyFlexOpen = row("ewy-xo", "2026-07-21T13:46:00Z", {
  ...EWY, action: "SELL_TO_OPEN", contracts: 25, fill_price: EWY_OPEN_PRICE,
  commission: EWY_OPEN_COMMISSION_TOTAL, ib_exec_id: "ewyo1+ewyo2",
  open_basis: 25 * EWY_OPEN_PRICE * 100 - EWY_OPEN_COMMISSION_TOTAL,
});
const ewyFillClose = row("ewy-fc1", "2026-07-23T14:30:00Z", {
  ...EWY, action: "BUY_TO_CLOSE", contracts: 25, fill_price: EWY_CLOSE_PRICE,
  commission: EWY_CLOSE_COMMISSION, ib_exec_id: "ewyc1",
});

const EWY_ALL = [ewyFillOpen1, ewyFillOpen2, ewyFlexOpen, ewyFillClose];

/* ── XYZ: fully closed BEFORE the window (07-15 open, 07-17 close) ───────── */

const XYZ = { ticker: "XYZ", strike: 50, right: "C", expiry: "20260918" };
const xyzOpen = row("xyz-o", "2026-07-15T14:00:00Z", {
  ...XYZ, action: "BUY_OPTION", contracts: 2, fill_price: 1.0, commission: 1.0, ib_exec_id: "xyzo1",
});
const xyzClose = row("xyz-c", "2026-07-17T14:00:00Z", {
  ...XYZ, action: "SELL_OPTION", contracts: 2, fill_price: 2.0, commission: 1.0, ib_exec_id: "xyzc1",
});

const COMBINED = [...SNDK_ALL, ...EWY_ALL, xyzOpen, xyzClose];
const GRAND_TOTAL = SNDK_EXPECTED + EWY_EXPECTED;

describe("classifyRowFamily", () => {
  it("classifies composite exec ids and basis-field carriers as flex_agg", () => {
    expect(classifyRowFamily(sndkFlexOpen.payload)).toBe("flex_agg");
    expect(classifyRowFamily(sndkFlexClose.payload)).toBe("flex_agg");
    expect(classifyRowFamily(sndkFillOpen1.payload)).toBe("fill");
    expect(classifyRowFamily(ewyFillClose.payload)).toBe("fill");
  });
});

describe("dedupJournalRows", () => {
  it("drops per-fill twins of composite Flex aggregates", () => {
    const { rows, droppedFillDupes } = dedupJournalRows(SNDK_ALL);
    expect(droppedFillDupes).toBe(4);
    expect(rows.map((r) => r.trade_id).sort()).toEqual(["sndk-xc", "sndk-xo"]);
  });

  it("dedupes across an IB correction suffix on the fill id", () => {
    const corrected = {
      ...sndkFillClose1,
      payload: { ...sndkFillClose1.payload, ib_exec_id: "sndkc1.01" },
    };
    const { rows, droppedFillDupes } = dedupJournalRows([corrected, sndkFlexClose]);
    expect(droppedFillDupes).toBe(1);
    expect(rows.map((r) => r.trade_id)).toEqual(["sndk-xc"]);
  });

  it("drops exact trade_id duplicates", () => {
    const { rows } = dedupJournalRows([sndkFillOpen1, sndkFillOpen1]);
    expect(rows).toHaveLength(1);
  });
});

describe("computeRealizedPnl", () => {
  it("reconstructs the SNDK cross-week 4-of-5 cover, attributing P&L to the close date", () => {
    const summary = computeRealizedPnl(SNDK_ALL, WINDOW);
    expect(summary.count).toBe(1);
    const trip = summary.round_trips[0];
    expect(trip.ticker).toBe("SNDK");
    expect(trip.opened).toBe("2026-07-16");
    expect(trip.closed).toBe("2026-07-21");
    expect(trip.qty).toBe(4);
    expect(trip.open_outside_window).toBe(true);
    expect(trip.realized_pnl).toBeCloseTo(SNDK_EXPECTED, 2);
    expect(trip.realized_pnl).toBeCloseTo(30689.16, 2);
    expect(trip.basis).toBeCloseTo(4 * sndkOpenNetCreditPerContract, 2);
    expect(trip.proceeds).toBeCloseTo(4 * SNDK_CLOSE_PRICE * 100 + SNDK_CLOSE_COMMISSION_TOTAL, 2);
    expect(summary.total_realized_pnl).toBeCloseTo(SNDK_EXPECTED, 2);
  });

  it("never double-counts: both families, fill-only, and flex-only all reconstruct the same P&L", () => {
    const both = computeRealizedPnl(SNDK_ALL, WINDOW).total_realized_pnl;
    const fillsOnly = computeRealizedPnl(SNDK_FILLS_ONLY, WINDOW).total_realized_pnl;
    const flexOnly = computeRealizedPnl(SNDK_FLEX_ONLY, WINDOW).total_realized_pnl;
    expect(both).toBeCloseTo(SNDK_EXPECTED, 2);
    expect(fillsOnly).toBeCloseTo(both, 2);
    expect(flexOnly).toBeCloseTo(both, 2);
  });

  it("computes the EWY same-week round trip from raw fills when no realized_pnl exists anywhere", () => {
    const summary = computeRealizedPnl(EWY_ALL, WINDOW);
    expect(summary.count).toBe(1);
    const trip = summary.round_trips[0];
    expect(trip.ticker).toBe("EWY");
    expect(trip.opened).toBe("2026-07-21");
    expect(trip.closed).toBe("2026-07-23");
    expect(trip.qty).toBe(25);
    expect(trip.open_outside_window).toBeUndefined();
    expect(trip.realized_pnl).toBeCloseTo(EWY_EXPECTED, 2);
    expect(trip.realized_pnl).toBeCloseTo(7220.79, 2);
  });

  it("pins the combined incident total and excludes the pre-window XYZ round trip", () => {
    const summary = computeRealizedPnl(COMBINED, WINDOW);
    expect(summary.count).toBe(2);
    expect(summary.round_trips.map((t) => t.ticker).sort()).toEqual(["EWY", "SNDK"]);
    expect(summary.total_realized_pnl).toBeCloseTo(GRAND_TOTAL, 2);
    expect(summary.total_realized_pnl).toBeCloseTo(37909.95, 2);
  });

  it("includes the XYZ trip when the window covers its close date", () => {
    const summary = computeRealizedPnl([xyzOpen, xyzClose], { from: "2026-07-13", to: "2026-07-18" });
    expect(summary.count).toBe(1);
    const expected = (2 * 2.0 * 100 - 1.0) - (2 * 1.0 * 100 + 1.0);
    expect(summary.round_trips[0].realized_pnl).toBeCloseTo(expected, 2);
    expect(summary.round_trips[0].closed).toBe("2026-07-17");
  });

  it("filters by ticker", () => {
    const summary = computeRealizedPnl(COMBINED, { ...WINDOW, ticker: "EWY" });
    expect(summary.count).toBe(1);
    expect(summary.round_trips[0].ticker).toBe("EWY");
    expect(summary.total_realized_pnl).toBeCloseTo(EWY_EXPECTED, 2);
  });

  it("passes a CLOSED aggregate with numeric realized_pnl through verbatim, no inventory feed", () => {
    const closedAgg = row("qqq-x", "2026-07-22T15:00:00Z", {
      ticker: "QQQ", strike: 500, right: "C", expiry: "20261218",
      action: "CLOSED", contracts: 10, fill_price: 5.0, commission: 2.5,
      realized_pnl: 123.45, cost_basis: 100.0, proceeds: 223.45, realized_quantity: 10,
      ib_exec_id: "qqqx1",
    });
    const summary = computeRealizedPnl([closedAgg], WINDOW);
    expect(summary.count).toBe(1);
    expect(summary.round_trips[0].realized_pnl).toBeCloseTo(123.45, 2);
    expect(summary.round_trips[0].qty).toBe(10);
    expect(summary.round_trips[0].closed).toBe("2026-07-22");
    expect(summary.total_realized_pnl).toBeCloseTo(123.45, 2);
  });

  it("reports net-of-commissions in the note", () => {
    const summary = computeRealizedPnl(SNDK_ALL, WINDOW);
    expect(summary.note).toMatch(/net of commissions/i);
    expect(summary.note).toMatch(/dedup/i);
  });
});

describe("cross-family commission sign conventions (live 2026-07-24 regression)", () => {
  // Verified against live rows: Flex aggregates store commission POSITIVE with
  // total_cost = gross + commission and proceeds = gross - commission; daemon
  // rows fold commissions into total_cost with INCONSISTENT signs (EWY open
  // -11.93, EWY close +17.28, SNDK close -1.27 rebate). Reconstructing cash
  // from fill_price +/- commission overstated the EWY credit by 2x commission
  // (7,244.65 instead of 7,220.79). The exec value must come from the
  // authoritative net field: proceeds for Flex sells, total_cost otherwise.
  const window = { from: "2026-07-20", to: "2026-07-24" };

  it("Flex SELL open kept after dedup uses net proceeds, never gross total_cost", () => {
    const rows = [
      {
        trade_id: "9915480873",
        filled_at: "2026-07-21T14:00:00Z",
        payload: {
          ticker: "EWY", action: "SELL_TO_OPEN",
          structure: "Short Call $175 2026-07-24",
          contracts: 25, fill_price: 5.0, right: "C", strike: 175, expiry: "20260724",
          total_cost: 12511.9278, proceeds: 12488.0722, commission: 11.9277,
          realized_pnl: 0, open_basis: 12488.0722, cost_basis: 0, realized_quantity: 0,
          ib_exec_id: "9915480873",
        },
      },
      {
        trade_id: "000205d2.6a61f71f.01.01",
        filled_at: "2026-07-23T15:30:00Z",
        payload: {
          ticker: "EWY", action: "BUY_TO_CLOSE",
          structure: "Closed Call $175 2026-07-24",
          contracts: 25, fill_price: 2.1, right: "C", strike: 175, expiry: "20260724",
          total_cost: 5267.2825, commission: 17.2825,
          ib_exec_id: "000205d2.6a61f71f.01.01",
        },
      },
    ] as never[];
    const summary = computeRealizedPnl(rows, window);
    expect(summary.count).toBe(1);
    // 12,488.07 net credit - 5,267.28 close debit; the gross-total_cost bug read +7,244.65.
    expect(summary.round_trips[0].realized_pnl).toBeCloseTo(7220.79, 2);
    expect(summary.round_trips[0].basis).toBeCloseTo(12488.07, 2);
  });

  it("daemon rebate close (negative commission) uses total_cost as-is", () => {
    const rows = [
      {
        trade_id: "open-1",
        filled_at: "2026-07-20T14:00:00Z",
        payload: {
          ticker: "XYZ", action: "SELL_TO_OPEN", contracts: 4, fill_price: 100.0,
          right: "P", strike: 1500, expiry: "20260821",
          total_cost: 39998.0, commission: -2.0,
          ib_exec_id: "open-1",
        },
      },
      {
        trade_id: "close-1",
        filled_at: "2026-07-22T15:00:00Z",
        payload: {
          ticker: "XYZ", action: "BUY_TO_CLOSE", contracts: 4, fill_price: 60.0,
          right: "P", strike: 1500, expiry: "20260821",
          // rebate: total_cost BELOW gross notional (24,000 - 1.27)
          total_cost: 23998.73, commission: -1.27,
          ib_exec_id: "close-1",
        },
      },
    ] as never[];
    const summary = computeRealizedPnl(rows, window);
    expect(summary.count).toBe(1);
    expect(summary.round_trips[0].realized_pnl).toBeCloseTo(39998.0 - 23998.73, 2);
  });
});
