/**
 * @vitest-environment jsdom
 *
 * Assistant order surface — full nine-field quote telemetry.
 *
 * ChatPanel routes an assistant order proposal through <ApprovalGate>, which is
 * the operator's LAST look before the order is placed. Until now the gate
 * showed prose only: the operator confirmed a live order with no bid/ask in
 * front of them. The gate now carries the same nine-field block the portfolio
 * position drawer renders (BID MID ASK / SPREAD LAST / VOLUME HIGH LOW DAY),
 * built by the shared OrderQuoteTelemetry component.
 *
 * Pinned here:
 *   1. ApprovalGate renders an optional `quote` slot, and stays unchanged when
 *      no caller passes one (it is a shared primitive with other callers).
 *   2. ChatPanel resolves the proposal's own instrument out of `prices`:
 *      stock -> ticker, single-leg option -> optionKey, combo -> the net quote.
 *   3. A combo reads MARK (a BAG is not a quoted instrument) and never borrows
 *      the underlying's session OHLV.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import ApprovalGate from "@/components/agent/ApprovalGate";
import ChatPanel from "@/components/ChatPanel";
import type { PriceData } from "@/lib/pricesProtocol";

// TEST_AUDIT T-177: the four `findByRole(..., { timeout: 8000 })` waits below
// are only reachable when the per-test timeout exceeds 8000. Vitest's default
// is 5000 and `vitest.config.ts` sets none, so a slow run died at 5s with an
// opaque "Test timed out in 5000ms" and the locator's own ceiling never fired
// — the operator never saw WHICH element was missing. Raised per FILE (the
// T-161 pattern used by dashboard-newsfeed-pagination and theta-harvester-
// scanner), never suite-wide, because `retry: 0` means a slow test has to be
// honest about being slow.
//
// The 8s ceiling is kept, not dropped: measured 2026-08-28 on the macOS gate,
// this file alone, the `confirm` wait itself ran 115 / 127 / 233 / 390 ms on a
// warm run, 435 / 600 / 636 / 1672 ms cold, and 605 / 1063 / 1309 / 1916 ms
// under `--coverage`. The 1.9s peak is already ~2x React Testing Library's
// 1000ms default, so deleting the argument would trade an opaque timeout for a
// flaky one on a loaded shard.
vi.setConfig({ testTimeout: 10_000, hookTimeout: 10_000 });

afterEach(cleanup);

const NINE_LABELS = ["BID", "MID", "ASK", "SPREAD", "VOLUME", "HIGH", "LOW", "DAY"];

function makePriceData(overrides: Partial<PriceData> & { symbol: string }): PriceData {
  return {
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
    ...overrides,
  };
}

const OPTION_PROPOSAL = {
  tool: "place_order",
  destructive: true as const,
  input: {
    type: "option" as const,
    ticker: "WULF",
    action: "BUY" as const,
    quantity: 10,
    limit_price: 5.6,
    expiry: "20260918",
    strike: 6,
    right: "C" as const,
    conId: 12345,
    exchange: "SMART",
  },
  summary: "BUY 10 WULF long call @ 5.6",
  toolUseId: "tu-1",
};

const STOCK_PROPOSAL = {
  tool: "place_order",
  destructive: true as const,
  input: {
    type: "stock" as const,
    ticker: "MU",
    action: "BUY" as const,
    quantity: 100,
    limit_price: 120,
  },
  summary: "BUY 100 MU @ 120",
  toolUseId: "tu-2",
};

const COMBO_PROPOSAL = {
  tool: "place_order",
  destructive: true as const,
  input: {
    type: "combo" as const,
    ticker: "MU",
    action: "BUY" as const,
    quantity: 5,
    limit_price: 2.1,
    structure: "Bull Call Spread",
    legs: [
      { expiry: "20260918", strike: 120, right: "C" as const, action: "BUY" as const, ratio: 1 },
      { expiry: "20260918", strike: 130, right: "C" as const, action: "SELL" as const, ratio: 1 },
    ],
  },
  summary: "BUY 5 MU bull call spread @ 2.10 debit",
  toolUseId: "tu-3",
};

const PRICES: Record<string, PriceData> = {
  WULF_20260918_6_C: makePriceData({
    symbol: "WULF_20260918_6_C",
    bid: 5.4,
    ask: 5.8,
    last: 5.6,
    close: 5.1,
    volume: 1420,
    high: 6.05,
    low: 5.2,
  }),
  MU: makePriceData({
    symbol: "MU",
    bid: 119.9,
    ask: 120.1,
    last: 120.05,
    close: 118.4,
    volume: 8_120_000,
    high: 121.2,
    low: 118.1,
  }),
  MU_20260918_120_C: makePriceData({
    symbol: "MU_20260918_120_C",
    bid: 8.0,
    ask: 8.4,
    last: 8.2,
  }),
  MU_20260918_130_C: makePriceData({
    symbol: "MU_20260918_130_C",
    bid: 5.9,
    ask: 6.3,
    last: 6.1,
  }),
};

function mockAssistantProposal(proposal: unknown) {
  const fetchMock = vi.fn(async (url: string) => {
    const body = url.includes("/api/assistant")
      ? { content: "Proposing an order.", proposal }
      : { content: "ok" };
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  // @ts-expect-error test stub
  global.fetch = fetchMock;
}

async function sendPrompt(text: string) {
  const textarea = screen.getByLabelText("Ask Radon");
  fireEvent.change(textarea, { target: { value: text } });
  fireEvent.submit(textarea.closest("form")!);
}

function priceBarLabels(): string[] {
  return Array.from(document.querySelectorAll(".price-bar-label")).map(
    (el) => el.textContent ?? "",
  );
}

describe("ApprovalGate — quote slot", () => {
  it("renders the telemetry node handed to it, above the handling options", () => {
    render(
      <ApprovalGate
        body="BUY 10 WULF 2026-09-18 $6 C @ 5.60 debit."
        options={[{ id: "route", label: "Route as proposed" }]}
        quote={<div className="price-bar"><span className="price-bar-label">BID</span></div>}
        onConfirm={() => {}}
        onDismiss={() => {}}
      />,
    );
    const gateBody = document.querySelector(".approval-gate__body")!;
    const nodes = Array.from(gateBody.children);
    const quoteIndex = nodes.findIndex((n) => n.classList.contains("price-bar"));
    const optionsIndex = nodes.findIndex((n) => n.classList.contains("approval-gate__options"));
    expect(quoteIndex).toBeGreaterThan(-1);
    expect(quoteIndex).toBeLessThan(optionsIndex);
  });

  it("renders no quote block for callers that pass none", () => {
    render(
      <ApprovalGate
        body="Nothing to quote."
        options={[{ id: "route", label: "Route as proposed" }]}
        onConfirm={() => {}}
        onDismiss={() => {}}
      />,
    );
    expect(priceBarLabels()).toHaveLength(0);
    expect(screen.getByText("Route as proposed")).toBeTruthy();
  });
});

describe("ChatPanel proposal gate — nine-field telemetry", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the full nine fields for a single-leg option proposal", async () => {
    mockAssistantProposal(OPTION_PROPOSAL);
    render(
      <ChatPanel
        activeSection="dashboard"
        portfolio={{ positions: [] } as never}
        prices={PRICES}
      />,
    );
    await sendPrompt("buy me some wulf calls");

    await screen.findByRole("button", { name: /confirm/i }, { timeout: 8000 });
    const labels = priceBarLabels();
    for (const label of NINE_LABELS) expect(labels).toContain(label);
    expect(labels).toContain("LAST");
    expect(labels).toContain("WULF 2026-09-18 $6 C");
    expect(screen.getByText("$5.40")).toBeTruthy();
    expect(screen.getByText("$5.80")).toBeTruthy();
    expect(screen.getByText("1,420")).toBeTruthy();
  });

  it("quotes the underlying for a stock proposal", async () => {
    mockAssistantProposal(STOCK_PROPOSAL);
    render(
      <ChatPanel
        activeSection="dashboard"
        portfolio={{ positions: [] } as never}
        prices={PRICES}
      />,
    );
    await sendPrompt("buy me 100 mu");

    await screen.findByRole("button", { name: /confirm/i }, { timeout: 8000 });
    const labels = priceBarLabels();
    for (const label of NINE_LABELS) expect(labels).toContain(label);
    expect(labels).toContain("MU");
    expect(screen.getByText("$119.90")).toBeTruthy();
    expect(screen.getByText("$120.10")).toBeTruthy();
  });

  it("quotes the net combo as MARK and leaves session OHLV empty", async () => {
    mockAssistantProposal(COMBO_PROPOSAL);
    render(
      <ChatPanel
        activeSection="dashboard"
        portfolio={{ positions: [] } as never}
        prices={PRICES}
      />,
    );
    await sendPrompt("put on a mu bull call spread");

    await screen.findByRole("button", { name: /confirm/i }, { timeout: 8000 });
    const labels = priceBarLabels();
    for (const label of NINE_LABELS) expect(labels).toContain(label);
    expect(labels).toContain("MARK");
    expect(labels).not.toContain("LAST");
    expect(labels).toContain("MU Bull Call Spread");
    // net bid = 8.00 - 6.30, net ask = 8.40 - 5.90, mid = 2.10
    expect(screen.getByText("$1.70")).toBeTruthy();
    expect(screen.getByText("$2.50")).toBeTruthy();
    expect(screen.getAllByText("$2.10").length).toBeGreaterThan(0);
  });

  it("renders an honest empty panel when no quote has arrived", async () => {
    mockAssistantProposal(OPTION_PROPOSAL);
    render(<ChatPanel activeSection="dashboard" portfolio={{ positions: [] } as never} />);
    await sendPrompt("buy me some wulf calls");

    await screen.findByRole("button", { name: /confirm/i }, { timeout: 8000 });
    expect(priceBarLabels()).toHaveLength(0);
    expect(screen.getByText("No real-time data")).toBeTruthy();
  });
});
