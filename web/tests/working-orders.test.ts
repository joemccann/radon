import { describe, expect, it } from "vitest";
import {
  filterWorkingOpenOrders,
  isPriorSessionDayOrder,
  isWorkingOrderMissingDetail,
  workingOrderMissingMessage,
} from "../lib/orders/workingOrders";

const now = new Date("2026-08-14T13:01:00Z"); // 09:01 ET

describe("isPriorSessionDayOrder", () => {
  it("drops a DAY order whose snapshot is a prior ET session", () => {
    expect(isPriorSessionDayOrder(
      { tif: "DAY" },
      "2026-08-13T19:59:51.850279Z",
      now,
    )).toBe(true);
  });

  it("keeps a DAY order snapshotted today ET", () => {
    expect(isPriorSessionDayOrder(
      { tif: "DAY" },
      "2026-08-14T13:30:00Z",
      now,
    )).toBe(false);
  });

  it("keeps a GTC order from a prior session", () => {
    expect(isPriorSessionDayOrder(
      { tif: "GTC" },
      "2026-08-13T19:59:51Z",
      now,
    )).toBe(false);
  });

  it("keeps a DAY order when snapshot time is missing", () => {
    expect(isPriorSessionDayOrder({ tif: "DAY" }, "", now)).toBe(false);
  });
});

describe("filterWorkingOpenOrders", () => {
  it("drops the CBRS Thursday DAY ghost and keeps Friday GTC", () => {
    const kept = filterWorkingOpenOrders(
      [
        { permId: 1857171561, tif: "DAY", symbol: "CBRS P230", snapshotAt: "2026-08-13T19:59:51Z" },
        { permId: 2128244184, tif: "GTC", symbol: "META P560", snapshotAt: "2026-08-13T19:59:51Z" },
      ],
      now,
    );
    expect(kept.map((o) => o.permId)).toEqual([2128244184]);
  });
});

describe("working-order missing copy", () => {
  it("names DAY close for a DAY order", () => {
    expect(workingOrderMissingMessage("DAY")).toMatch(/DAY orders end/i);
  });

  it("stays generic when TIF is not DAY", () => {
    expect(workingOrderMissingMessage("GTC")).toMatch(/filled or been cancelled/i);
  });

  it("detects IB not-found detail", () => {
    expect(isWorkingOrderMissingDetail("Trade not found (orderId=291, permId=1857171561)")).toBe(true);
    expect(isWorkingOrderMissingDetail("Order is no longer working at IB")).toBe(true);
    expect(isWorkingOrderMissingDetail("Modify not confirmed")).toBe(false);
  });
});
