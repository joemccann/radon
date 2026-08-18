/**
 * @vitest-environment jsdom
 */

import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import PnlBreakdownModal from "@/components/PnlBreakdownModal";

afterEach(() => cleanup());

describe("MetricBreakdownModal sort", () => {
  it("reorders declarative rows when TICKER is clicked", () => {
    render(
      <PnlBreakdownModal
        open
        title="Unrealized P&L"
        formula="Market value - entry cost"
        col1Header="Entry Cost"
        col2Header="Mkt Value"
        rows={[
          { id: 1, ticker: "MSFT", structure: "Stock", col1: "$1", col2: "$2", pnl: 1 },
          { id: 2, ticker: "AAPL", structure: "Stock", col1: "$3", col2: "$4", pnl: 40 },
          { id: 3, ticker: "NVDA", structure: "Stock", col1: "$5", col2: "$6", pnl: 8 },
        ]}
        total={49}
        onClose={() => undefined}
      />,
    );
    const first = () => within(screen.getByRole("table")).getAllByRole("row")[1].textContent ?? "";
    expect(first()).toContain("AAPL");
    fireEvent.click(screen.getByRole("columnheader", { name: /ticker/i }));
    expect(first()).toContain("AAPL");
    fireEvent.click(screen.getByRole("columnheader", { name: /ticker/i }));
    expect(first()).toContain("NVDA");
    expect(screen.getByRole("columnheader", { name: /ticker/i }).getAttribute("aria-sort")).toBe("descending");
  });
});
