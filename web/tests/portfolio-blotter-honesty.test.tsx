/**
 * @vitest-environment jsdom
 *
 * REL-091 — missing data must not render as a confident number.
 *
 * R-242: the three risk buckets are exact string matches and every section is
 * gated on `length > 0`, so a position whose `risk_profile` is null, absent,
 * or a new producer value falls into no bucket and is rendered NOWHERE. The
 * pills each report only their own bucket, so nothing reconciles them against
 * `positions.length` and the page looks complete.
 *
 * R-243: with a non-null portfolio and `positions: []`, all three guards render
 * nothing and the sole output is `Last Sync ... • Source: IB Gateway` — a clean
 * page asserting a successful sync at a recent wall-clock time over a flat
 * book. "Degraded snapshot" and "I am flat" are byte-identical on screen, and
 * the footer actively reinforces the wrong reading.
 *
 * R-244: `LegRow`'s `sign` is applied to `legPnl` and to the Avg Entry / Last
 * Price display, but NOT to the Market Value, Entry Cost or Initial Value
 * cells — while the parent row's equivalents are signed. A credit spread shows
 * a header Initial Value of −$400 above leg rows reading $1,200 and $1,600.
 *
 * R-248: the blotter realized-P&L cell computes its colour from
 * `(t.realized_pnl ?? 0) >= 0` while the text guards on `!= null`, so a null
 * P&L renders `---` painted GREEN — indistinguishable from a profitable close
 * on a blotter scanned by colour.
 *
 * R-270: Last Price is `mv / (contracts * multiplier)` with no zero guard, so
 * a flattened-mid-sync row prints `$∞` or `$NaN` and drives the price flash.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import PortfolioSections from "@/components/PortfolioSections";
import PositionTable, { POSITION_COLUMN_DEFAULTS } from "@/components/PositionTable";
import { HistoricalTradesSection } from "@/components/WorkspaceSections";
import { seedRiskFreeRateForTests } from "@/lib/useRiskFreeRate";
import type { BlotterData, PortfolioData, PortfolioPosition } from "@/lib/types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/portfolio",
}));

vi.mock("@/components/InstrumentDetailModal", () => ({ default: () => null }));

const useBlotterMock = vi.fn();
vi.mock("@/lib/useBlotter", () => ({
  useBlotter: (...args: unknown[]) => useBlotterMock(...args),
}));

afterEach(() => {
  useBlotterMock.mockReset();
  cleanup();
});

/** Signed number behind a formatted cell. `fmtUsd` puts the minus outside the
 *  `$` and `fmtPrice` inside it, so read the sign off the parsed value rather
 *  than off a leading character. */
function cellNumber(text: string): number {
  const parsed = Number(text.replace(/[$,\s]/g, ""));
  expect(Number.isFinite(parsed)).toBe(true);
  return parsed;
}

/** Index of a table header by its label, so cell lookups survive column moves. */
function columnIndex(label: string): number {
  const header = screen.getByText(label).closest("th")!;
  return Array.from(header.parentElement!.children).indexOf(header);
}

function position(overrides: Partial<PortfolioPosition>): PortfolioPosition {
  return {
    id: 1,
    ticker: "AMD",
    structure: "Long Call",
    structure_type: "Long Call",
    direction: "LONG",
    contracts: 1,
    expiry: "2026-05-01",
    entry_date: "2026-01-02",
    entry_cost: 1000,
    market_value: 1200,
    market_price_is_calculated: false,
    risk_profile: "defined",
    legs: [],
    ...overrides,
  } as unknown as PortfolioPosition;
}

function portfolio(positions: PortfolioPosition[]): PortfolioData {
  return {
    last_sync: "2026-08-26T14:00:00Z",
    positions,
    net_liq: 100000,
  } as unknown as PortfolioData;
}

describe("PortfolioSections", () => {
  it("renders a position whose risk_profile matches no bucket", () => {
    seedRiskFreeRateForTests(0);
    const orphan = position({ id: 7, ticker: "ORPH", risk_profile: null as never });
    render(<PortfolioSections portfolio={portfolio([orphan])} prices={{}} />);
    expect(screen.queryByText(/ORPH/)).not.toBeNull();
  });

  it("reconciles the rendered count against positions.length", () => {
    seedRiskFreeRateForTests(0);
    const positions = [
      position({ id: 1, ticker: "AAA", risk_profile: "defined" }),
      position({ id: 2, ticker: "BBB", risk_profile: "brand-new-producer-value" as never }),
    ];
    const { container } = render(
      <PortfolioSections portfolio={portfolio(positions)} prices={{}} />,
    );
    expect(container.textContent).toContain("BBB");
  });

  it("does not render a bare live-source footer over an empty book", () => {
    seedRiskFreeRateForTests(0);
    const { container } = render(
      <PortfolioSections portfolio={portfolio([])} prices={{}} />,
    );
    const text = container.textContent ?? "";
    expect(text).toContain("Source: IB Gateway");
    expect(text).toMatch(/no open positions/i);
  });
});

/** Credit spread: the SHORT leg carries the larger premium, so the combo's
 *  Initial Value is a net CREDIT and every short-leg money cell must read
 *  negative underneath it. */
const CREDIT_SPREAD: PortfolioPosition = {
  id: 91,
  ticker: "CRDT",
  structure: "Bear Call Spread $250.0/$300.0",
  structure_type: "Bear Call Spread",
  risk_profile: "defined",
  direction: "COMBO",
  expiry: "2099-05-01",
  entry_date: "2026-01-02",
  contracts: 1,
  entry_cost: -400,
  market_value: -400,
  legs: [
    {
      direction: "LONG",
      contracts: 1,
      type: "Call",
      strike: 300,
      entry_cost: 1200,
      avg_cost: 1200,
      market_price: 14,
      market_value: 1400,
    },
    {
      direction: "SHORT",
      contracts: 1,
      type: "Call",
      strike: 250,
      entry_cost: -1600,
      avg_cost: -1600,
      market_price: 18,
      market_value: -1800,
    },
  ],
} as unknown as PortfolioPosition;

/** One closed trade per realized-P&L state: unknown, profit, loss. */
function blotter(): BlotterData {
  const trade = (symbol: string, realized_pnl: number | null) => ({
    symbol,
    contract_desc: `${symbol} 20990101 100C`,
    sec_type: "OPT",
    is_closed: true,
    net_quantity: 0,
    total_commission: 1.5,
    realized_pnl,
    cost_basis: 1000,
    proceeds: 1000,
    total_cash_flow: 0,
    executions: [
      {
        exec_id: `${symbol}-1`,
        time: "2026-03-24T10:10:00.000Z",
        side: "SLD",
        quantity: 1,
        price: 10,
        commission: 1.5,
        notional_value: 1000,
        net_cash_flow: -998.5,
      },
    ],
  });
  return {
    as_of: "2026-03-25T17:00:00.000Z",
    summary: { closed_trades: 3, open_trades: 0, total_commissions: 4.5, realized_pnl: 0 },
    closed_trades: [trade("NULLP", null), trade("WINNR", 120), trade("LOSER", -120)],
    open_trades: [],
  } as unknown as BlotterData;
}

describe("PositionTable — leg cells and non-finite prices", () => {
  it("signs the leg Market Value / Entry Cost / Initial Value cells", () => {
    seedRiskFreeRateForTests(0);
    render(
      <PositionTable
        positions={[CREDIT_SPREAD]}
        prices={{}}
        columnVisibility={{ ...POSITION_COLUMN_DEFAULTS, entry_cost: true }}
      />,
    );
    fireEvent.click(screen.getByLabelText("Expand legs for CRDT"));

    const mvIndex = columnIndex("Market Value");
    const ecIndex = columnIndex("Entry Cost");
    const ivIndex = columnIndex("Initial Value");

    const longCells = Array.from(screen.getByText("LONG Call $300").closest("tr")!.children);
    const shortCells = Array.from(screen.getByText("SHORT Call $250").closest("tr")!.children);

    // The SHORT leg is a CREDIT: all three money cells read negative, matching
    // the signed combo header. The LONG leg's debit stays positive. An inverted
    // `sign` keeps the leg cells signed but flips which leg is the credit, so
    // the header no longer reconciles against its own legs. R-244.
    expect(cellNumber(shortCells[mvIndex]!.textContent ?? "")).toBeLessThan(0);
    expect(cellNumber(shortCells[ecIndex]!.textContent ?? "")).toBeLessThan(0);
    expect(cellNumber(shortCells[ivIndex]!.textContent ?? "")).toBeLessThan(0);

    expect(cellNumber(longCells[mvIndex]!.textContent ?? "")).toBeGreaterThan(0);
    expect(cellNumber(longCells[ecIndex]!.textContent ?? "")).toBeGreaterThan(0);
    expect(cellNumber(longCells[ivIndex]!.textContent ?? "")).toBeGreaterThan(0);
  });

  it("never divides by a zero contract count", async () => {
    const { readFileSync } = await import("fs");
    const { join } = await import("path");
    const src = readFileSync(join(__dirname, "..", "components", "PositionTable.tsx"), "utf-8");
    const body = src
      .split("\n")
      .filter((l) => !l.trim().startsWith("//") && !l.trim().startsWith("*"))
      .join("\n");
    expect(body).not.toMatch(/\/ \(pos\.contracts \* getMultiplier\(pos\)\)/);
    expect(body).toContain("finiteOrNull");
  });
});

describe("SharePnlData — an unknown P&L stays unknown", () => {
  it("does not coerce a null realized P&L to zero", async () => {
    const { readFileSync } = await import("fs");
    const { join } = await import("path");
    const src = readFileSync(join(__dirname, "..", "components", "WorkspaceSections.tsx"), "utf-8");
    const body = src
      .split("\n")
      .filter((l) => !l.trim().startsWith("//") && !l.trim().startsWith("*"))
      .join("\n");
    expect(body).not.toContain("pnl: e.realizedPNL ?? 0");
    expect(body).not.toContain("pnl: group.totalPnL ?? 0");
    expect(body).not.toContain("const realizedPnl = t.realized_pnl ?? 0;");
  });

  it("colours the blotter realized-P&L cell only when it is known", () => {
    useBlotterMock.mockReturnValue({
      data: blotter(),
      loading: false,
      syncing: false,
      error: null,
      syncNow: vi.fn(),
    });
    render(<HistoricalTradesSection />);

    const pnlIndex = columnIndex("Realized P&L");
    const cellFor = (symbol: string) =>
      Array.from(screen.getByText(symbol).closest("tr")!.children)[pnlIndex]! as HTMLElement;

    // UNKNOWN: the text says `---`, so the colour must say nothing either.
    const unknown = cellFor("NULLP");
    expect(unknown.textContent).toBe("---");
    expect(unknown.className).not.toContain("positive");
    expect(unknown.className).not.toContain("negative");

    // Known values still carry their colour, so the assertion above is not
    // passing because colouring was removed wholesale.
    expect(cellFor("WINNR").className).toContain("positive");
    expect(cellFor("LOSER").className).toContain("negative");
  });
});
