import { describe, it, expect } from "vitest";
import { applyDepthOp } from "./depthLadder.js";

const L = (...prices) => prices.map((p, i) => ({ price: p, size: 10 + i, marketMaker: null }));
const prices = (ladder) => ladder.map((l) => l.price);

describe("applyDepthOp — IB position-shift semantics (T-041)", () => {
  it("insert at 0 shifts every level down", () => {
    const ladder = L(99, 98);
    const r = applyDepthOp(ladder, { operation: 0, position: 0, price: 100, size: 5 }, 10);
    expect(r.applied).toBe(true);
    expect(prices(ladder)).toEqual([100, 99, 98]);
  });

  it("insert at exactly length appends", () => {
    const ladder = L(100, 99);
    const r = applyDepthOp(ladder, { operation: 0, position: 2, price: 98, size: 5 }, 10);
    expect(r.applied).toBe(true);
    expect(prices(ladder)).toEqual([100, 99, 98]);
  });

  it("insert beyond length is a desync, not a tail-clamp", () => {
    const ladder = L(100, 99);
    const r = applyDepthOp(ladder, { operation: 0, position: 5, price: 42, size: 5 }, 10);
    expect(r.applied).toBe(false);
    expect(prices(ladder)).toEqual([100, 99]);
  });

  it("insert past the row budget truncates the tail", () => {
    const ladder = L(100, 99, 98);
    const r = applyDepthOp(ladder, { operation: 0, position: 0, price: 101, size: 5 }, 3);
    expect(r.applied).toBe(true);
    expect(prices(ladder)).toEqual([101, 100, 99]);
    expect(ladder.length).toBe(3);
  });

  it("update edits strictly in place", () => {
    const ladder = L(100, 99, 98);
    const r = applyDepthOp(ladder, { operation: 1, position: 1, price: 99.5, size: 7 }, 10);
    expect(r.applied).toBe(true);
    expect(prices(ladder)).toEqual([100, 99.5, 98]);
    expect(ladder.length).toBe(3);
  });

  it("OOB update never degrades to an insert", () => {
    const ladder = L(100, 99);
    const r = applyDepthOp(ladder, { operation: 1, position: 4, price: 42, size: 5 }, 10);
    expect(r.applied).toBe(false);
    expect(prices(ladder)).toEqual([100, 99]);
  });

  it("delete shifts the levels below up one", () => {
    const ladder = L(100, 99, 98);
    const r = applyDepthOp(ladder, { operation: 2, position: 0, price: 0, size: 0 }, 10);
    expect(r.applied).toBe(true);
    expect(prices(ladder)).toEqual([99, 98]);
  });

  it("OOB delete is rejected and mutates nothing", () => {
    const ladder = L(100);
    const r = applyDepthOp(ladder, { operation: 2, position: 3, price: 0, size: 0 }, 10);
    expect(r.applied).toBe(false);
    expect(prices(ladder)).toEqual([100]);
  });

  it("replays IB's ground-truth sequence exactly", () => {
    // insert 100, insert 99 below, insert 99.5 between, update middle,
    // delete top — the ladder must track IB's book at every step.
    const ladder = [];
    applyDepthOp(ladder, { operation: 0, position: 0, price: 100, size: 1 }, 10);
    applyDepthOp(ladder, { operation: 0, position: 1, price: 99, size: 1 }, 10);
    applyDepthOp(ladder, { operation: 0, position: 1, price: 99.5, size: 1 }, 10);
    expect(prices(ladder)).toEqual([100, 99.5, 99]);
    applyDepthOp(ladder, { operation: 1, position: 1, price: 99.6, size: 2 }, 10);
    expect(prices(ladder)).toEqual([100, 99.6, 99]);
    applyDepthOp(ladder, { operation: 2, position: 0, price: 0, size: 0 }, 10);
    expect(prices(ladder)).toEqual([99.6, 99]);
  });

  it("rejects negative and non-integer positions", () => {
    const ladder = L(100);
    expect(applyDepthOp(ladder, { operation: 0, position: -1, price: 1, size: 1 }, 10).applied).toBe(false);
    expect(applyDepthOp(ladder, { operation: 1, position: 0.5, price: 1, size: 1 }, 10).applied).toBe(false);
    expect(prices(ladder)).toEqual([100]);
  });

  it("unknown operations are rejected", () => {
    const ladder = L(100);
    expect(applyDepthOp(ladder, { operation: 7, position: 0, price: 1, size: 1 }, 10).applied).toBe(false);
  });
});
