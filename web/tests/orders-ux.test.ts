/**
 * @vitest-environment jsdom
 *
 * Pure UX helpers for /orders: shortcuts, bulk selection, page size.
 */
import { describe, expect, it } from "vitest";
import type { OpenOrder } from "../lib/types";
import type { OpenOrderDisplayRow } from "../lib/openOrderCombos";
import {
  canModifyDisplayRow,
  flattenSelectedOpenOrders,
  formatShowingRange,
  isEditableKeyboardTarget,
  openOrderRowKey,
  ordersFromDisplayRow,
  parseHistoricalPageSize,
  parseOpenOrdersDensity,
  resolveOrdersShortcut,
  setAllSelectionKeys,
  toggleSelectionKey,
} from "../lib/orders/ordersUx";

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
  };
}

const singleRow: OpenOrderDisplayRow = {
  kind: "single",
  order: makeOrder({ orderId: 7, permId: 700 }),
  index: 0,
  summary: null,
};

const comboRow: OpenOrderDisplayRow = {
  kind: "combo",
  id: "combo-aapl-1",
  index: 1,
  symbol: "AAPL",
  structure: "Vertical",
  summary: "100/110 C",
  orders: [
    makeOrder({ orderId: 10, permId: 1000, action: "BUY" }),
    makeOrder({ orderId: 11, permId: 1001, action: "SELL" }),
  ],
  totalQuantity: 5,
  orderType: "LMT",
  status: "Submitted",
  tif: "DAY",
  limitPrice: 1.2,
};

describe("openOrderRowKey / ordersFromDisplayRow", () => {
  it("keys single and combo rows stably", () => {
    expect(openOrderRowKey(singleRow)).toBe("7-700");
    expect(openOrderRowKey(comboRow)).toBe("combo-aapl-1");
  });

  it("flattens combo legs and wraps single", () => {
    expect(ordersFromDisplayRow(singleRow)).toHaveLength(1);
    expect(ordersFromDisplayRow(comboRow)).toHaveLength(2);
  });
});

describe("canModifyDisplayRow", () => {
  it("requires all combo legs to be modifiable", () => {
    const canLmt = (o: OpenOrder) => o.orderType === "LMT";
    expect(canModifyDisplayRow(singleRow, canLmt)).toBe(true);
    expect(canModifyDisplayRow(comboRow, canLmt)).toBe(true);
    expect(
      canModifyDisplayRow(
        {
          ...comboRow,
          orders: [
            makeOrder({ orderId: 1, permId: 1, orderType: "LMT" }),
            makeOrder({ orderId: 2, permId: 2, orderType: "MKT" }),
          ],
        },
        canLmt,
      ),
    ).toBe(false);
  });
});

describe("isEditableKeyboardTarget", () => {
  it("detects input/textarea/select/contenteditable", () => {
    const input = document.createElement("input");
    const textarea = document.createElement("textarea");
    const select = document.createElement("select");
    const div = document.createElement("div");
    const editable = document.createElement("div");
    editable.contentEditable = "true";
    // jsdom: isContentEditable tracks contentEditable in many environments
    Object.defineProperty(editable, "isContentEditable", { value: true });

    expect(isEditableKeyboardTarget(input)).toBe(true);
    expect(isEditableKeyboardTarget(textarea)).toBe(true);
    expect(isEditableKeyboardTarget(select)).toBe(true);
    expect(isEditableKeyboardTarget(div)).toBe(false);
    expect(isEditableKeyboardTarget(editable)).toBe(true);
    expect(isEditableKeyboardTarget(null)).toBe(false);
  });
});

describe("resolveOrdersShortcut", () => {
  it("maps / to focus-filter", () => {
    expect(
      resolveOrdersShortcut({
        key: "/",
        isTyping: false,
        hasModifier: false,
        selectedRowKey: null,
        canModifySelected: false,
      }),
    ).toEqual({ type: "focus-filter" });
  });

  it("ignores shortcuts while typing or with modifiers", () => {
    expect(
      resolveOrdersShortcut({
        key: "/",
        isTyping: true,
        hasModifier: false,
        selectedRowKey: "1-1",
        canModifySelected: true,
      }),
    ).toBeNull();
    expect(
      resolveOrdersShortcut({
        key: "m",
        isTyping: false,
        hasModifier: true,
        selectedRowKey: "1-1",
        canModifySelected: true,
      }),
    ).toBeNull();
  });

  it("requires a selected row for M and X", () => {
    expect(
      resolveOrdersShortcut({
        key: "m",
        isTyping: false,
        hasModifier: false,
        selectedRowKey: null,
        canModifySelected: true,
      }),
    ).toBeNull();
    expect(
      resolveOrdersShortcut({
        key: "x",
        isTyping: false,
        hasModifier: false,
        selectedRowKey: null,
        canModifySelected: true,
      }),
    ).toBeNull();
  });

  it("maps m/M to modify when canModify, else null", () => {
    expect(
      resolveOrdersShortcut({
        key: "M",
        isTyping: false,
        hasModifier: false,
        selectedRowKey: "7-700",
        canModifySelected: true,
      }),
    ).toEqual({ type: "modify" });
    expect(
      resolveOrdersShortcut({
        key: "m",
        isTyping: false,
        hasModifier: false,
        selectedRowKey: "7-700",
        canModifySelected: false,
      }),
    ).toBeNull();
  });

  it("maps x/X to cancel with selection", () => {
    expect(
      resolveOrdersShortcut({
        key: "x",
        isTyping: false,
        hasModifier: false,
        selectedRowKey: "combo-aapl-1",
        canModifySelected: false,
      }),
    ).toEqual({ type: "cancel" });
  });
});

describe("selection helpers", () => {
  it("toggles keys and sets all", () => {
    let set = new Set<string>();
    set = toggleSelectionKey(set, "a", true);
    set = toggleSelectionKey(set, "b", true);
    expect([...set].sort()).toEqual(["a", "b"]);
    set = toggleSelectionKey(set, "a", false);
    expect([...set]).toEqual(["b"]);
    expect([...setAllSelectionKeys(["x", "y"], true)].sort()).toEqual(["x", "y"]);
    expect(setAllSelectionKeys(["x", "y"], false).size).toBe(0);
  });

  it("flattens selected rows for bulk cancel dialog", () => {
    const other: OpenOrderDisplayRow = {
      kind: "single",
      order: makeOrder({ orderId: 99, permId: 999, symbol: "MSFT" }),
      index: 2,
      summary: null,
    };
    const orders = flattenSelectedOpenOrders(
      [singleRow, comboRow, other],
      new Set([openOrderRowKey(singleRow), openOrderRowKey(comboRow)]),
    );
    expect(orders.map((o) => o.permId).sort((a, b) => a - b)).toEqual([700, 1000, 1001]);
  });
});

describe("historical page size + showing range", () => {
  it("parses only 15/30/50", () => {
    expect(parseHistoricalPageSize("30")).toBe(30);
    expect(parseHistoricalPageSize("50")).toBe(50);
    expect(parseHistoricalPageSize("15")).toBe(15);
    expect(parseHistoricalPageSize("99")).toBe(15);
    expect(parseHistoricalPageSize(null)).toBe(15);
  });

  it("formats showing range without em dash", () => {
    expect(formatShowingRange(0, 15, 42)).toBe("Showing 1-15 of 42");
    expect(formatShowingRange(1, 15, 42)).toBe("Showing 16-30 of 42");
    expect(formatShowingRange(2, 15, 42)).toBe("Showing 31-42 of 42");
    expect(formatShowingRange(0, 15, 0)).toBe("Showing 0 of 0");
    expect(formatShowingRange(0, 15, 42)).not.toMatch(/\u2014/);
  });

  it("parses density", () => {
    expect(parseOpenOrdersDensity("compact")).toBe("compact");
    expect(parseOpenOrdersDensity(null)).toBe("comfortable");
    expect(parseOpenOrdersDensity("nope")).toBe("comfortable");
  });
});
