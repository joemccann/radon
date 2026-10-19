/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BlotterData, BlotterTrade } from "../lib/types";
import { blotterTradesToCsv, blotterCsvFilename } from "../lib/blotter/csv";
import { HistoricalTradesSection } from "../components/WorkspaceSections";

vi.mock("../components/TickerLink", () => ({
  default: (props: { ticker: string }) => React.createElement("span", null, props.ticker),
}));

const useBlotterMock = vi.fn();
vi.mock("../lib/useBlotter", () => ({
  useBlotter: (...args: unknown[]) => useBlotterMock(...args),
}));

const saveArtifactMock = vi.fn();
vi.mock("../lib/researchWorkbench/artifacts", () => ({
  saveArtifact: (...args: unknown[]) => saveArtifactMock(...args),
}));

function trade(symbol: string, day: number, overrides: Partial<BlotterTrade> = {}): BlotterTrade {
  return {
    symbol,
    contract_desc: `${symbol} Long Call $20 2026-10-20`,
    sec_type: "OPT",
    is_closed: true,
    net_quantity: 0,
    total_quantity: 10,
    total_commission: 1.25,
    realized_pnl: 100,
    cost_basis: 1000,
    proceeds: 1100,
    total_cash_flow: 100,
    executions: [{
      exec_id: `${symbol}-${day}`,
      time: `2026-09-${String(day).padStart(2, "0")}`,
      side: "SLD",
      quantity: 10,
      price: 1.1,
      commission: 1.25,
      notional_value: 1100,
      net_cash_flow: 1098.75,
    }],
    ...overrides,
  };
}

const HEADER = "Date,Symbol,Description,Type,Status,Qty,Avg Fill,Commission,Realized P&L,Cost Basis,Proceeds";

describe("blotterTradesToCsv", () => {
  it("writes a header and one raw-valued row per trade", () => {
    const csv = blotterTradesToCsv([trade("VIX", 1)]);
    expect(csv.split("\r\n")).toEqual([
      HEADER,
      "2026-09-01,VIX,VIX Long Call $20 2026-10-20,OPT,Closed,10,1.1,1.25,100,1000,1100",
    ]);
  });

  it("leaves unknown values empty instead of printing zero", () => {
    const t = trade("VIX", 1, { realized_pnl: null, is_closed: false });
    t.executions[0].price = null;
    const [, row] = blotterTradesToCsv([t]).split("\r\n");
    expect(row).toBe("2026-09-01,VIX,VIX Long Call $20 2026-10-20,OPT,Open,10,,1.25,,1000,1100");
  });

  it("quotes fields containing commas, quotes or newlines", () => {
    const t = trade("VIX", 1, { contract_desc: 'VIX "Spread", 20/23.5' });
    const [, row] = blotterTradesToCsv([t]).split("\r\n");
    expect(row).toContain(',"VIX ""Spread"", 20/23.5",');
  });

  it("neutralises spreadsheet formula prefixes", () => {
    const t = trade("=HYPERLINK(1)", 1);
    const [, row] = blotterTradesToCsv([t]).split("\r\n");
    expect(row.split(",")[1]).toBe("'=HYPERLINK(1)");
  });

  it("names the file after the blotter date", () => {
    expect(blotterCsvFilename("2026-10-19")).toBe("radon-historical-trades-2026-10-19.csv");
  });
});

describe("HistoricalTradesSection CSV download", () => {
  const trades = Array.from({ length: 30 }, (_, i) => trade(i % 2 ? "VIX" : "SPY", i + 1));
  const data: BlotterData = {
    as_of: "2026-10-19",
    summary: { closed_trades: 30, open_trades: 0, total_commissions: 0, realized_pnl: 0 },
    closed_trades: trades,
    open_trades: [],
  };

  beforeEach(() => {
    window.localStorage.clear();
    useBlotterMock.mockReturnValue({ data, loading: false, syncing: false, error: null, syncNow: vi.fn() });
  });
  afterEach(() => {
    useBlotterMock.mockReset();
    saveArtifactMock.mockReset();
    cleanup();
  });

  async function downloadedCsv(): Promise<string[]> {
    fireEvent.click(screen.getByRole("button", { name: /download csv/i }));
    expect(saveArtifactMock).toHaveBeenCalledTimes(1);
    const [blob, name] = saveArtifactMock.mock.calls[0] as [Blob, string];
    expect(name).toBe("radon-historical-trades-2026-10-19.csv");
    expect(blob.type).toBe("text/csv;charset=utf-8");
    return (await blob.text()).split("\r\n");
  }

  it("downloads every row, not just the visible page", async () => {
    render(React.createElement(HistoricalTradesSection));
    expect(screen.getAllByRole("row").length - 1).toBe(15);
    const lines = await downloadedCsv();
    expect(lines[0]).toBe(HEADER);
    expect(lines).toHaveLength(31);
  });

  it("downloads the filtered set in table order", async () => {
    render(React.createElement(HistoricalTradesSection));
    fireEvent.change(screen.getByPlaceholderText("Filter historical trades..."), { target: { value: "vix" } });
    const lines = await downloadedCsv();
    expect(lines).toHaveLength(16);
    expect(lines.slice(1).every((l) => l.split(",")[1] === "VIX")).toBe(true);
    expect(lines[1].startsWith("2026-09-30,")).toBe(true);
  });

  it("does not toggle the section when clicked", () => {
    render(React.createElement(HistoricalTradesSection));
    fireEvent.click(screen.getByRole("button", { name: /download csv/i }));
    expect(screen.getByTestId("historical-trades-toggle").getAttribute("aria-expanded")).toBe("true");
  });
});
