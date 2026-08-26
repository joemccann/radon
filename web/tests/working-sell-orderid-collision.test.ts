/**
 * @vitest-environment node
 *
 * R-207 — `orderId` is not an identity, and treating it as one under-counts
 * working SELL units.
 *
 * `isExcludedOrder` OR'd the two id branches rather than treating `orderId` as
 * a fallback. `permId` is globally unique at IB; `orderId` is per-client-session
 * and is reused across `clientId`s and across restarts, and `open_orders` is a
 * full multi-client snapshot. So when the permId branch misses, the orderId
 * branch can still match a DIFFERENT working SELL BAG on the same ticker and
 * subtract its quantity from `workingSellUnits`. That reopens the R-112
 * over-close hole for that order: `findHeldComboForClose` sees more free held
 * units than exist, sets `closeOut`, and `whatIfKey` returns null whenever
 * `closeOut != null`, so the broker what-if never runs to catch it and
 * `okToSubmit` is asserted on structure match alone.
 *
 * `ModifyOrderModal` always supplies `permId`, so the `orderId` branch should
 * be reachable only when `permId == null`.
 */
import { describe, it, expect } from "vitest";

import { workingSellComboUnits } from "../lib/order/positionTrade";

function bagOrder(permId: number, orderId: number, quantity: number) {
  return {
    permId,
    orderId,
    action: "SELL",
    totalQuantity: quantity,
    contract: { secType: "BAG", symbol: "SLV" },
  };
}

/** Two working SELL BAGs on one ticker, placed by different clientIds, whose
 *  per-session orderIds happen to collide. */
const orders = {
  open_orders: [
    bagOrder(900_100, 7, 4), // the one being modified
    bagOrder(900_200, 7, 6), // a different order, same reused orderId
  ],
};

describe("workingSellComboUnits exclusion", () => {
  it("excludes only the named order when permId identifies it", () => {
    const units = workingSellComboUnits("SLV", orders, { permId: 900_100, orderId: 7 });
    expect(units).toBe(6);
  });

  it("does not let a colliding orderId hide a second working SELL", () => {
    // permId misses (this order is not in the snapshot), so the orderId branch
    // fires and silently removes 10 units of genuine working exposure.
    const units = workingSellComboUnits("SLV", orders, { permId: 900_999, orderId: 7 });
    expect(units).toBe(10);
  });

  it("still falls back to orderId when there is no permId to match on", () => {
    const withoutPermId = {
      open_orders: [
        { ...bagOrder(0, 7, 4), permId: null },
        bagOrder(900_200, 8, 6),
      ],
    };
    const units = workingSellComboUnits("SLV", withoutPermId, { permId: null, orderId: 7 });
    expect(units).toBe(6);
  });

  it("counts everything when nothing is excluded", () => {
    expect(workingSellComboUnits("SLV", orders, null)).toBe(10);
  });
});
