// @vitest-environment jsdom
//
// The locate chip renders inside the order confirm step. It read
// `data.source.toUpperCase()` behind a `!== "none"` guard, which passes for
// `undefined` - so a short-availability payload missing `source` threw and took
// the whole order ticket down at the moment of confirmation, on a live trading
// surface. Degrade to no source line instead.

import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";

import { LocateFeeChip } from "@/lib/order/components/LocateFeeChip";

afterEach(cleanup);

const BASE = {
  symbol: "MU",
  shortable_shares: 100_000,
  fee_rate: 1.2,
  as_of: new Date().toISOString(),
  source: "ib",
} as never;

describe("LocateFeeChip with a partial payload", () => {
  it("renders when source is present", () => {
    const { container } = render(<LocateFeeChip status="available" data={BASE} />);
    expect(container.textContent).toContain("IB");
  });

  it("survives a payload with no source rather than crashing the ticket", () => {
    const partial = { ...(BASE as object), source: undefined } as never;
    expect(() => render(<LocateFeeChip status="available" data={partial} />)).not.toThrow();
  });

  it("survives a payload with no timestamp", () => {
    const partial = { ...(BASE as object), as_of: undefined } as never;
    expect(() => render(<LocateFeeChip status="available" data={partial} />)).not.toThrow();
  });
});
