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
import { cleanup, render, screen } from "@testing-library/react";

import PortfolioSections from "@/components/PortfolioSections";
import { seedRiskFreeRateForTests } from "@/lib/useRiskFreeRate";
import type { PortfolioData, PortfolioPosition } from "@/lib/types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/portfolio",
}));

afterEach(cleanup);

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

describe("PositionTable — leg cells and non-finite prices", () => {
  it("signs the leg Market Value / Entry Cost / Initial Value cells", async () => {
    const { readFileSync } = await import("fs");
    const { join } = await import("path");
    const src = readFileSync(join(__dirname, "..", "components", "PositionTable.tsx"), "utf-8");
    const body = src
      .split("\n")
      .filter((l) => !l.trim().startsWith("//") && !l.trim().startsWith("*") && !l.trim().startsWith("{/*"))
      .join("\n");
    // The three unsigned renders are gone.
    expect(body).not.toContain("{legMv != null ? fmtUsd(legMv) : \"—\"}");
    expect(body).not.toContain("{fmtPrice(legEc)}");
    expect(body).not.toContain("{fmtUsd(legEc)}");
    expect(body).toContain("sign * legEc");
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

  it("colours the blotter realized-P&L cell only when it is known", async () => {
    const { readFileSync } = await import("fs");
    const { join } = await import("path");
    const src = readFileSync(join(__dirname, "..", "components", "WorkspaceSections.tsx"), "utf-8");
    const body = src
      .split("\n")
      .filter((l) => !l.trim().startsWith("//") && !l.trim().startsWith("*") && !l.trim().startsWith("{/*"))
      .join("\n");
    expect(body).not.toContain('(t.realized_pnl ?? 0) >= 0 ? "positive" : "negative"');
  });
});
