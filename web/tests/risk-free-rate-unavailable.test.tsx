/**
 * @vitest-environment jsdom
 *
 * R-229 — a missing risk-free rate must not read as 0%.
 *
 * `getSnapshot()` was `cachedRate ?? 0`, so "FRED says the effective Fed Funds
 * rate is 0%" and "FRED never answered" were the same value to every consumer.
 * The route answers any failure with `{rate: 0, source: "fallback", stale: true}`
 * at HTTP 200; the hook correctly refuses to cache that but then left
 * `cachedRate === null` with only a `useEffect(..., [])` that fires once per
 * consumer mount, and `loadRiskFreeRate` short-circuits on `inFlight` — no
 * timer, no retry, no backoff, no error state. On a long-lived WorkspaceShell
 * session one transient FRED miss at page load pinned `r = 0` for the rest of
 * the session, and `PositionTable` feeds that straight into
 * `computePositionImpliedValue` for the Implied columns the operator compares
 * against Last Price. Every function in the chain also defaults
 * `riskFreeRate = 0`, so the fallback was indistinguishable from a real
 * observation at every layer.
 */

import { act } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PositionTable, {
  POSITION_COLUMN_DEFAULTS,
  type PositionColumnVisibility,
} from "../components/PositionTable";
import { bsCall, bsPut } from "../lib/blackScholes";
import { yearsToExpiry } from "../lib/impliedValue";
import {
  resetRiskFreeRateCacheForTests,
  seedRiskFreeRateForTests,
  useRiskFreeRate,
  useRiskFreeRateState,
} from "../lib/useRiskFreeRate";
import type { PriceData } from "../lib/pricesProtocol";
import type { PortfolioPosition } from "../lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const FALLBACK = { rate: 0, source: "fallback", stale: true };
const REAL = { rate: 0.0433, source: "FRED:DFF", stale: false };

function StateProbe() {
  const { rate, resolved } = useRiskFreeRateState();
  return (
    <output data-testid="probe">{resolved ? String(rate) : "unresolved"}</output>
  );
}

beforeEach(() => {
  resetRiskFreeRateCacheForTests();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  cleanup();
  resetRiskFreeRateCacheForTests();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("useRiskFreeRateState", () => {
  it("reports unresolved rather than 0 when the route serves its fallback", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => FALLBACK }));
    render(<StateProbe />);
    await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("unresolved"));
  });

  it("retries on a timer instead of waiting for another consumer to mount", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => FALLBACK })
      .mockResolvedValue({ ok: true, json: async () => REAL });
    vi.stubGlobal("fetch", fetchMock);

    render(<StateProbe />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // Nothing else mounts, nothing re-renders. Only an internal timer can
    // recover this session.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(70_000);
    });

    await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("0.0433"));
  });

  it("backs off rather than hammering a persistently failing route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => FALLBACK });
    vi.stubGlobal("fetch", fetchMock);

    render(<StateProbe />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60_000);
    });

    const calls = fetchMock.mock.calls.length;
    expect(calls).toBeGreaterThan(1);
    expect(calls).toBeLessThan(12);
  });

  it("stops retrying once a real observation lands", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => REAL });
    vi.stubGlobal("fetch", fetchMock);

    render(<StateProbe />);
    await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("0.0433"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60_000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("useRiskFreeRate — the legacy number contract", () => {
  it("still hands consumers a usable number", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => REAL }));
    function NumberProbe() {
      return <output data-testid="n">{useRiskFreeRate()}</output>;
    }
    render(<NumberProbe />);
    await waitFor(() => expect(screen.getByTestId("n").textContent).toBe("0.0433"));
  });
});

/* ─── PositionTable — rendered Implied cells ─────────────────────────────
 *
 * The previous version of this block grepped PositionTable.tsx for
 * `riskFreeRate == null` and required five matches. That counts guards; it
 * does not price anything. A SIXTH call site added without a guard still
 * counted five and passed, and a guard rewritten to `return 0` also still
 * counted five and passed — both of which put a Black-Scholes number priced
 * off r = 0 into a cell the operator reads as an observation. Render the
 * table instead and assert the cells.
 */

/** ~10 months out, so T > 0 whenever this test runs and r visibly moves BS. */
const EXPIRY = new Date(Date.now() + 300 * 86_400_000).toISOString().slice(0, 10);
const OKEY = EXPIRY.replace(/-/g, "");
const SPOT = 405;
const SIGMA = 0.45;

function pd(over: Partial<PriceData>): PriceData {
  return {
    symbol: "X",
    last: null,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: new Date().toISOString(),
    ...over,
  };
}

const PRICES: Record<string, PriceData> = {
  TSLA: pd({ last: SPOT }),
  [`TSLA_${OKEY}_410_C`]: pd({ impliedVol: SIGMA }),
  [`TSLA_${OKEY}_400_P`]: pd({ impliedVol: SIGMA }),
};

const RATIO_RR: PortfolioPosition = {
  id: 13,
  ticker: "TSLA",
  structure: "Ratio Risk Reversal 75x10 (P$400.0/C$410.0)",
  structure_type: "Ratio Risk Reversal",
  risk_profile: "undefined",
  expiry: EXPIRY,
  contracts: 75,
  direction: "COMBO",
  entry_cost: 118200,
  max_risk: null,
  market_value: 51975,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-04-15",
  legs: [
    {
      direction: "LONG",
      contracts: 75,
      type: "Call",
      strike: 410,
      entry_cost: 145875,
      avg_cost: 1945,
      market_price: 10.45,
      market_value: 78375,
    },
    {
      direction: "SHORT",
      contracts: 10,
      type: "Put",
      strike: 400,
      entry_cost: -27690,
      avg_cost: -2769,
      market_price: 26.41,
      market_value: -26410,
    },
  ],
};

/** Implied MV is hidden by default; make both Implied columns visible. */
const COLUMNS: PositionColumnVisibility = {
  ...POSITION_COLUMN_DEFAULTS,
  implied_market_value: true,
};

/** Text of EVERY cell under the given header, across header + leg rows. */
function cellsUnder(headerLabel: string): string[] {
  const headers = Array.from(document.querySelectorAll("thead th"));
  const index = headers.findIndex((th) => th.textContent?.trim() === headerLabel);
  expect(index).toBeGreaterThanOrEqual(0);
  return Array.from(document.querySelectorAll("tbody tr")).map(
    (row) => row.children[index]?.textContent?.trim() ?? "<missing>",
  );
}

function impliedCells(): string[] {
  return [...cellsUnder("Implied"), ...cellsUnder("Implied MV")];
}

/** Independent copies of the render formatters, so a formatter change cannot
 *  silently redefine what this test considers a "real value". */
const price = (v: number) =>
  `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const signedUsd = (v: number) =>
  `${v >= 0 ? "+" : "-"}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;

/** Header net + both leg cells for Implied, then the same for Implied MV. */
function expectedCells(r: number): string[] {
  const T = yearsToExpiry(EXPIRY, new Date())!;
  const call = bsCall(SPOT, 410, T, r, SIGMA);
  const put = bsPut(SPOT, 400, T, r, SIGMA);
  const callMv = call * 75 * 100;
  const putMv = put * 10 * 100;
  return [
    price(call - put),
    price(call),
    price(-put),
    signedUsd(callMv - putMv),
    signedUsd(callMv),
    signedUsd(-putMv),
  ];
}

function renderTable() {
  render(<PositionTable positions={[RATIO_RR]} prices={PRICES} columnVisibility={COLUMNS} />);
  fireEvent.click(screen.getByLabelText("Expand legs for TSLA"));
}

describe("PositionTable Implied columns", () => {
  it("renders every Implied cell as '—' while the route serves its fallback", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => FALLBACK });
    vi.stubGlobal("fetch", fetchMock);

    renderTable();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    // Six cells: header + two legs, for Implied and Implied MV. Every one of
    // them must be blank, not a number priced off r = 0.
    const cells = impliedCells();
    expect(cells).toHaveLength(6);
    expect(cells).toEqual(["—", "—", "—", "—", "—", "—"]);
  });

  it("prices every Implied cell off the rate once FRED resolves", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => REAL }));
    seedRiskFreeRateForTests(0.0433);

    renderTable();

    const cells = impliedCells();
    expect(cells).not.toContain("—");
    expect(cells).toEqual(expectedCells(0.0433));
    // The seeded rate really reaches Black-Scholes: r = 0 prices differently.
    expect(cells).not.toEqual(expectedCells(0));
  });
});
