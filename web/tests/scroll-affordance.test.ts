import { describe, expect, it } from "vitest";
import { computeScrollAffordance } from "../lib/scrollAffordance";

describe("computeScrollAffordance", () => {
  it("no overflow: neither side scrollable", () => {
    expect(computeScrollAffordance(0, 300, 300)).toEqual({ left: false, right: false });
  });

  it("overflow at start: right only", () => {
    expect(computeScrollAffordance(0, 900, 393)).toEqual({ left: false, right: true });
  });

  it("mid-scroll: both sides", () => {
    expect(computeScrollAffordance(200, 900, 393)).toEqual({ left: true, right: true });
  });

  it("scrolled to end: left only", () => {
    expect(computeScrollAffordance(507, 900, 393)).toEqual({ left: true, right: false });
  });

  it("sub-pixel residue at the end still reads as end", () => {
    expect(computeScrollAffordance(506.4, 900, 393)).toEqual({ left: true, right: false });
  });
});
