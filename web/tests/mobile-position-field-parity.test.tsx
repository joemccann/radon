/**
 * @vitest-environment jsdom
 *
 * Mobile position cards, default-ON desktop field parity (2026-09-02).
 *
 * The collapsed mobile card used to drop several columns that are default-ON
 * in the desktop PositionTable: avg entry, position-level last price (+trend
 * arrows / calculated flag), Black-Scholes implied for options, day change
 * for STOCKS (options had it in the subtitle), the direction risk-profile
 * pill and a dedicated qty. Return % also rendered twice on the same card.
 *
 * These tests pin the parity overhaul: the scan set (ticker, pill, qty,
 * structure, expiry, last, P&L, Return %, MV, Today, EC, Day Chg) is on the
 * collapsed card; the basis set (Avg Entry, Implied) plus the full leg field
 * set (signed avg / last / implied / MV / initial value) is one tap away.
 * Every number must come from the same positionUtils / impliedValue helpers
 * the desktop table renders — no card-local math.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import MobilePositionList from "../components/mobile/MobilePositionList";
import { seedRiskFreeRateForTests } from "@/lib/useRiskFreeRate";
import { bsPut } from "../lib/blackScholes";
import { yearsToExpiry } from "../lib/impliedValue";
import type { PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));

afterEach(cleanup);

// R-229: Implied renders "—" until FRED resolves. Seed a RESOLVED 0% so the
// Black-Scholes arithmetic below is explicit, mirroring
// position-table-implied.test.tsx.
beforeEach(() => {
  seedRiskFreeRateForTests(0);
});

function pd(over: Partial<PriceData> = {}): PriceData {
  return {
    symbol: "X", last: null, lastIsCalculated: false, bid: null, ask: null, bidSize: null,
    askSize: null, volume: null, high: null, low: null, open: null, close: null, week52High: null,
    week52Low: null, avgVolume: null, delta: null, gamma: null, theta: null, vega: null,
    impliedVol: null, undPrice: null, timestamp: new Date().toISOString(), ...over,
  };
}

/** Overnight long stock — day chg must come from getStockDailyChg (last vs close). */
const MSFT_LONG_STOCK: PortfolioPosition = {
  id: 1, ticker: "MSFT", structure: "Stock (1000.0 shares)", structure_type: "Stock",
  risk_profile: "equity", expiry: "N/A", contracts: 1000, direction: "LONG",
  entry_cost: 463498, max_risk: 463498, market_value: 457690, kelly_optimal: null,
  target: null, stop: null, entry_date: "2026-06-01",
  legs: [{ direction: "LONG", contracts: 1000, type: "Stock", strike: 0, entry_cost: 463498, avg_cost: 463.498, market_price: 457.69, market_value: 457690 }],
};

/** Short stock — avg entry stays a positive per-instrument PRICE (daca786). */
const MU_SHORT_STOCK: PortfolioPosition = {
  id: 2, ticker: "MU", structure: "Stock (-1000.0 shares)", structure_type: "Stock",
  risk_profile: "equity", expiry: "N/A", contracts: 1000, direction: "SHORT",
  entry_cost: -1134969, max_risk: null, market_value: -1055360, kelly_optimal: null,
  target: null, stop: null, entry_date: "2026-06-01",
  legs: [{ direction: "SHORT", contracts: 1000, type: "Stock", strike: 0, entry_cost: 1134969, avg_cost: 1134.97, market_price: 1055.36, market_value: 1055360 }],
};

/** Live 2026-08-07 net-credit combo (the EC polarity fixture). */
const SPCX_CREDIT_COMBO: PortfolioPosition = {
  id: 3, ticker: "SPCX", structure: "Ratio Risk Reversal 60x30 (P$120.0/C$135.0)",
  structure_type: "Ratio Risk Reversal", risk_profile: "undefined", expiry: "2026-08-21",
  contracts: 60, direction: "COMBO", entry_cost: -1513.6, max_risk: null, market_value: 23550,
  kelly_optimal: null, target: null, stop: null, entry_date: "2026-07-28",
  legs: [
    { direction: "LONG", contracts: 60, type: "Call", strike: 135, entry_cost: 48429.07, avg_cost: 807.1511, market_price: 6.55, market_value: 39300 },
    { direction: "SHORT", contracts: 30, type: "Put", strike: 120, entry_cost: 49942.67, avg_cost: 1664.7556, market_price: 5.25, market_value: 15750 },
  ],
};

/** Single-leg SHORT call — avg entry is a premium CREDIT (negative). */
const EWY_SHORT_CALL: PortfolioPosition = {
  id: 4, ticker: "EWY", structure: "Short Call $165", structure_type: "Short Call",
  risk_profile: "undefined", expiry: "2026-09-18", contracts: 25, direction: "SHORT",
  entry_cost: 12518, max_risk: null, market_value: 10000, kelly_optimal: null,
  target: null, stop: null, entry_date: "2026-07-30",
  legs: [{ direction: "SHORT", contracts: 25, type: "Call", strike: 165, entry_cost: 12518, avg_cost: -500.7, market_price: 4.0, market_value: 10000 }],
};

const IMPLIED_EXPIRY = "2099-05-01"; // far future so T > 0 whenever the test runs

const AMD_LONG_PUT: PortfolioPosition = {
  id: 5, ticker: "AMD", structure: "Long Put $295", structure_type: "Long Put",
  risk_profile: "defined", expiry: IMPLIED_EXPIRY, contracts: 75, direction: "LONG",
  entry_cost: 22500, max_risk: 22500, market_value: null as unknown as number,
  kelly_optimal: null, target: null, stop: null, entry_date: "2026-04-25",
  legs: [{ direction: "LONG", contracts: 75, type: "Put", strike: 295, entry_cost: 22500, avg_cost: 3.0, market_price: 3.0, market_value: 22500 }],
};

function card(ticker: string): HTMLElement {
  return screen.getByTestId(`mobile-position-${ticker}`);
}

function expand(ticker: string): HTMLElement {
  const c = card(ticker);
  fireEvent.click(within(c).getByRole("button", { expanded: false }));
  return c;
}

/** The value under a labeled MetricCell / leg cell inside `root`. */
function labeledValue(root: HTMLElement, label: string): string {
  const node = within(root)
    .getAllByText(label)
    .find((el) => el.className.includes("__label") || el.className.includes("-k"));
  expect(node, `label ${label}`).toBeTruthy();
  return node!.parentElement?.textContent?.replace(label, "").trim() ?? "";
}

describe("collapsed card — position-level last price", () => {
  it("stock: renders the live tick as Last", () => {
    render(<MobilePositionList positions={[MSFT_LONG_STOCK]} prices={{ MSFT: pd({ last: 457.69, close: 450 }) }} />);
    expect(screen.getByTestId("mobile-position-MSFT-last").textContent).toContain("$457.69");
  });

  it("option combo: derives per-contract Last from the ONE shared market value (desktop formula)", () => {
    render(<MobilePositionList positions={[SPCX_CREDIT_COMBO]} prices={{ SPCX: pd({ last: 121.24 }) }} />);
    // mv 23,550 / (60 contracts × 100) = $3.93 — same as the desktop cell.
    expect(screen.getByTestId("mobile-position-SPCX-last").textContent).toContain("$3.93");
  });

  it("flags a calculated mark with the desktop C-prefix", () => {
    const calculated: PortfolioPosition = {
      ...EWY_SHORT_CALL,
      id: 6,
      legs: [{ ...EWY_SHORT_CALL.legs[0], market_price_is_calculated: true }],
    };
    render(<MobilePositionList positions={[calculated]} prices={{}} />);
    expect(screen.getByTestId("mobile-position-EWY-last").textContent).toContain("C$");
  });

  it("shows a trend arrow when the live price moves", () => {
    const { rerender } = render(
      <MobilePositionList positions={[MSFT_LONG_STOCK]} prices={{ MSFT: pd({ last: 450, close: 449 }) }} />,
    );
    expect(within(card("MSFT")).queryByLabelText("price up")).toBeNull();
    rerender(<MobilePositionList positions={[MSFT_LONG_STOCK]} prices={{ MSFT: pd({ last: 457.69, close: 449 }) }} />);
    expect(within(card("MSFT")).getByLabelText("price up")).toBeTruthy();
  });
});

describe("collapsed card — day change for stocks AND options", () => {
  it("stock: getStockDailyChg off yesterday's close", () => {
    render(<MobilePositionList positions={[MSFT_LONG_STOCK]} prices={{ MSFT: pd({ last: 457.69, close: 450 }) }} />);
    // (457.69 − 450) / 450 = +1.71%
    expect(screen.getByTestId("mobile-position-MSFT-daychg").textContent).toContain("+1.71%");
  });

  it("option: getOptionDailyChg off the legs' closes", () => {
    const prices = {
      EWY: pd({ last: 157.9 }),
      EWY_20260918_165_C: pd({ last: 4.0, close: 5.0 }),
    };
    render(<MobilePositionList positions={[EWY_SHORT_CALL]} prices={prices} />);
    // SHORT: sign −1 × (4 − 5) × 25 × 100 = +$2,500 on |close value| $12,500 = +20%
    expect(screen.getByTestId("mobile-position-EWY-daychg").textContent).toContain("+20.00%");
  });

  it("renders — when no close is available rather than inventing a baseline", () => {
    render(<MobilePositionList positions={[MSFT_LONG_STOCK]} prices={{ MSFT: pd({ last: 457.69 }) }} />);
    expect(screen.getByTestId("mobile-position-MSFT-daychg").textContent).toContain("—");
  });
});

describe("collapsed card — identity: direction pill, dedicated qty, structure, expiry", () => {
  it("renders the desktop risk-profile pill (defined/undefined/neutral)", () => {
    render(
      <MobilePositionList
        positions={[SPCX_CREDIT_COMBO, MU_SHORT_STOCK, AMD_LONG_PUT]}
        prices={{}}
      />,
    );
    expect(card("SPCX").querySelector(".pill.undefined")?.textContent).toBe("COMBO");
    expect(card("MU").querySelector(".pill.neutral")?.textContent).toBe("SHORT");
    expect(card("AMD").querySelector(".pill.defined")?.textContent).toBe("LONG");
  });

  it("renders a dedicated qty next to the pill", () => {
    render(<MobilePositionList positions={[SPCX_CREDIT_COMBO]} prices={{}} />);
    expect(screen.getByTestId("mobile-position-SPCX-qty").textContent).toBe("60x");
  });

  it("shows expiry in the subtitle and hides it when the section hides it (equity)", () => {
    const first = render(<MobilePositionList positions={[SPCX_CREDIT_COMBO]} prices={{}} />);
    expect(card("SPCX").textContent).toContain("2026-08-21");
    first.unmount();

    render(<MobilePositionList positions={[SPCX_CREDIT_COMBO]} prices={{}} showExpiry={false} />);
    expect(card("SPCX").textContent).not.toContain("2026-08-21");
  });
});

describe("collapsed card — Return % appears exactly once", () => {
  it("one labeled Return % per card, carrying the provenance title", () => {
    render(<MobilePositionList positions={[AMD_LONG_PUT]} prices={{}} />);
    const labels = within(card("AMD")).getAllByText("Return %");
    expect(labels).toHaveLength(1);
    // entry 22,500 → mv 22,500 → 0% of max risk 22,500
    const value = within(card("AMD")).getByText("0.00%");
    expect(value.parentElement?.getAttribute("title")).toContain("max risk");
  });
});

describe("expanded card — avg entry with desktop sign scoping", () => {
  it("net-credit combo: negative per-contract avg entry", () => {
    render(<MobilePositionList positions={[SPCX_CREDIT_COMBO]} prices={{}} />);
    expand("SPCX");
    // −1,513.60 / (60 × 100) = −$0.25
    expect(labeledValue(screen.getByTestId("mobile-position-SPCX-basis"), "Avg Entry")).toBe("$-0.25");
  });

  it("single-leg short option: premium credit reads negative", () => {
    render(<MobilePositionList positions={[EWY_SHORT_CALL]} prices={{}} />);
    expand("EWY");
    // 12,518 / (25 × 100) = $5.01, credited → −$5.01
    expect(labeledValue(screen.getByTestId("mobile-position-EWY-basis"), "Avg Entry")).toBe("$-5.01");
  });

  it("short stock: avg entry stays a positive per-instrument price", () => {
    render(<MobilePositionList positions={[MU_SHORT_STOCK]} prices={{}} />);
    expand("MU");
    expect(labeledValue(screen.getByTestId("mobile-position-MU-basis"), "Avg Entry")).toBe("$1,134.97");
  });
});

describe("expanded card — Black-Scholes implied for options", () => {
  it("renders the BS-derived implied per-contract price (desktop arithmetic)", () => {
    const sigma = 0.45;
    const spot = 280;
    const prices: Record<string, PriceData> = {
      AMD: pd({ last: spot }),
      [`AMD_${IMPLIED_EXPIRY.replace(/-/g, "")}_295_P`]: pd({ impliedVol: sigma }),
    };
    render(<MobilePositionList positions={[AMD_LONG_PUT]} prices={prices} />);
    expand("AMD");

    const T = yearsToExpiry(IMPLIED_EXPIRY, new Date())!;
    const expected = bsPut(spot, 295, T, 0, sigma).toFixed(2);
    expect(labeledValue(screen.getByTestId("mobile-position-AMD-basis"), "Implied")).toContain(expected);
  });

  it("renders — when IV is missing, and no Implied cell at all for a stock", () => {
    render(<MobilePositionList positions={[AMD_LONG_PUT, MSFT_LONG_STOCK]} prices={{ AMD: pd({ last: 280 }) }} />);
    expand("AMD");
    expect(labeledValue(screen.getByTestId("mobile-position-AMD-basis"), "Implied")).toBe("—");

    expand("MSFT");
    expect(within(screen.getByTestId("mobile-position-MSFT-basis")).queryByText("Implied")).toBeNull();
  });
});

describe("expanded legs — full default-ON leg field set, signed like desktop LegRow", () => {
  function spcxLegs(): HTMLElement {
    render(
      <MobilePositionList positions={[SPCX_CREDIT_COMBO]} prices={{ SPCX: pd({ last: 121.24 }) }} />,
    );
    expand("SPCX");
    return screen.getByTestId("mobile-position-SPCX-legs");
  }

  it("long leg: positive avg / last / MV / initial value", () => {
    const legs = spcxLegs();
    const long = within(legs).getByText("LONG 60x Call $135").closest(".mobile-card__leg") as HTMLElement;
    expect(labeledValue(long, "Avg")).toBe("$8.07");
    expect(labeledValue(long, "Last")).toBe("$6.55");
    expect(labeledValue(long, "MV")).toBe("$39,300");
    expect(labeledValue(long, "Init")).toBe("$48,429");
  });

  it("short leg: avg entry, last, MV and initial value are CREDITS (negative)", () => {
    const legs = spcxLegs();
    const short = within(legs).getByText("SHORT 30x Put $120").closest(".mobile-card__leg") as HTMLElement;
    // avg_cost 1,664.7556 / 100 = $16.65, credited → −$16.65
    expect(labeledValue(short, "Avg")).toBe("$-16.65");
    expect(labeledValue(short, "Last")).toBe("$-5.25");
    expect(labeledValue(short, "MV")).toBe("-$15,750");
    expect(labeledValue(short, "Init")).toBe("-$49,943");
  });

  it("stock leg of a covered call keeps a positive per-instrument price", () => {
    const coveredCall: PortfolioPosition = {
      id: 7, ticker: "KO", structure: "Covered Call $165.0 (2500 shares)", structure_type: "Covered Call",
      risk_profile: "defined", expiry: "2026-08-07", contracts: 2500, direction: "LONG",
      entry_cost: 412139, max_risk: 412139, market_value: 385500, kelly_optimal: null,
      target: null, stop: null, entry_date: "2026-07-28",
      legs: [
        { direction: "LONG", contracts: 2500, type: "Stock", strike: null, entry_cost: 424657, avg_cost: 169.86, market_price: 157.9, market_value: 394750 },
        { direction: "SHORT", contracts: 25, type: "Call", strike: 165, entry_cost: 12518, avg_cost: -500.7, market_price: 4.0, market_value: 10000 },
      ],
    };
    render(<MobilePositionList positions={[coveredCall]} prices={{ KO: pd({ last: 157.9 }) }} />);
    expand("KO");
    const stockLeg = within(screen.getByTestId("mobile-position-KO-legs"))
      .getByText("LONG 2500x Stock")
      .closest(".mobile-card__leg") as HTMLElement;
    expect(labeledValue(stockLeg, "Avg")).toBe("$169.86");
    expect(labeledValue(stockLeg, "Last")).toBe("$157.90");
  });
});

describe("expand/collapse is announced", () => {
  it("the card button toggles aria-expanded and reveals basis + legs", () => {
    render(<MobilePositionList positions={[SPCX_CREDIT_COMBO]} prices={{}} />);
    const button = within(card("SPCX")).getByRole("button", { name: /SPCX Ratio Risk Reversal/ });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("mobile-position-SPCX-basis")).toBeNull();

    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("mobile-position-SPCX-basis")).toBeTruthy();
    expect(screen.getByTestId("mobile-position-SPCX-legs")).toBeTruthy();
  });
});

describe("dead UI — ColumnsToggle is hidden on the mobile shell", () => {
  it("globals.css hides .columns-toggle under body[data-mobile=true]", () => {
    const css = readFileSync(join(__dirname, "..", "app", "globals.css"), "utf8");
    const rule = css.match(/body\[data-mobile="true"\]\s+\.columns-toggle\s*\{([^}]*)\}/m);
    expect(rule).not.toBeNull();
    expect(rule![1]).toContain("display: none");
  });
});
