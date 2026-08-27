/**
 * @vitest-environment jsdom
 *
 * R-209 — PositionTradeTicket's submit gate fails OPEN.
 *
 * `const okToSubmit = riskState?.okToSubmit !== false;` permits any state other
 * than an explicit `false`, including `null`. Every sibling surface uses
 * `=== true` (SingleLegOrderTicket, MobileOrderTicket, OrderTab,
 * OptionsChainTab, ListedContractOrderForm, ModifyOrderModal, ChatPanel) and
 * web/CLAUDE.md states the contract as "Parent surface MUST disable submit when
 * state.okToSubmit !== true".
 *
 * The null case is reachable, not theoretical. `<OrderRiskGate onState=...>` is
 * mounted only inside the confirm step and publishes through a post-commit
 * useEffect, so on the first commit of the confirm view `riskState` is still
 * null and "Confirm Order" is already enabled. And `riskState` is never
 * cleared — `reset()` and Back clear only `confirmStep` — so after backing out
 * and editing the order into a worse shape, the button that appears on the next
 * Review click is gated by the PREVIOUS shape's verdict. `handlePlace` re-checks
 * isValid, built and checkNakedShortRisk but never okToSubmit, so the `disabled`
 * attribute is the only thing between a coverage-unresolved order and
 * POST /api/orders/place.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

// Fault injection: the gate mounts but has not resolved a verdict yet. This is
// the real shape of the null window — OrderRiskGate publishes through a
// post-commit useEffect and `useOrderRisk` returns null until coverage
// resolves — and it is also what the operator sees for the first commit of
// every confirm view.
vi.mock("@/lib/order", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    OrderRiskGate: () => null,
  };
});

import PositionTradeTicket from "@/components/ticker-detail/PositionTradeTicket";
import { legPriceKey } from "@/lib/positionUtils";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioPosition } from "@/lib/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function makePriceData(overrides: Partial<PriceData> & { symbol: string }): PriceData {
  return {
    last: null, lastIsCalculated: false, bid: null, ask: null, bidSize: null,
    askSize: null, volume: null, high: null, low: null, open: null, close: null,
    week52High: null, week52Low: null, avgVolume: null, delta: null, gamma: null,
    theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

function riskReversal(): PortfolioPosition {
  return {
    id: 7,
    ticker: "MU",
    structure: "Risk Reversal (P$800.0/C$1050.0)",
    structure_type: "Risk Reversal",
    direction: "COMBO",
    contracts: 5,
    expiry: "2026-07-17",
    entry_date: "2026-05-29",
    entry_cost: -3495,
    market_value: -46290,
    market_price_is_calculated: false,
    legs: [
      { direction: "SHORT", type: "Call", strike: 1050, contracts: 3, avg_cost: 10999,
        entry_cost: -32997, market_price: 133.93, market_price_is_calculated: false },
      { direction: "LONG", type: "Put", strike: 800, contracts: 5, avg_cost: 5900,
        entry_cost: 29500, market_price: 41.0, market_price_is_calculated: false },
    ],
  } as unknown as PortfolioPosition;
}

function pricesFor(position: PortfolioPosition): Record<string, PriceData> {
  const callKey = legPriceKey(position.ticker, position.expiry, position.legs[0])!;
  const putKey = legPriceKey(position.ticker, position.expiry, position.legs[1])!;
  return {
    [callKey]: makePriceData({ symbol: callKey, bid: 132.0, ask: 136.0, last: 133.93, close: 130.0 }),
    [putKey]: makePriceData({ symbol: putKey, bid: 40.5, ask: 41.5, last: 41.0, close: 43.25 }),
  };
}

function renderTicket() {
  const position = riskReversal();
  return render(
    <PositionTradeTicket
      position={position}
      prices={pricesFor(position)}
      portfolio={null}
      target={{ kind: "leg", index: 1 }}
      onClose={() => {}}
    />,
  );
}

/** The ticket opens with an empty limit price, which fails `isValid`. */
function fillLimitPrice(container: HTMLElement, value = "41.00") {
  const inputs = [...container.querySelectorAll('input[placeholder="0.00"]')];
  fireEvent.change(inputs[inputs.length - 1], { target: { value } });
}

function reviewOrder(container: HTMLElement) {
  fillLimitPrice(container);
  fireEvent.click(screen.getByRole("button", { name: /review order/i }));
}

function confirmButton(): HTMLButtonElement | null {
  return screen.queryByRole("button", { name: /confirm order/i }) as HTMLButtonElement | null;
}

describe("PositionTradeTicket submit gate", () => {
  it("does not enable Confirm Order before the risk gate has published a verdict", () => {
    const { container } = renderTicket();
    reviewOrder(container);
    const button = confirmButton();
    expect(button).not.toBeNull();
    expect(button!.disabled).toBe(true);
  });

  it("re-gates after backing out, so a previous verdict cannot carry over", () => {
    const { container } = renderTicket();
    reviewOrder(container);
    fireEvent.click(screen.getByRole("button", { name: /^back$/i }));
    reviewOrder(container);
    expect(confirmButton()!.disabled).toBe(true);
  });

  it("never POSTs to /api/orders/place while the verdict is unresolved", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    );
    const { container } = renderTicket();
    reviewOrder(container);
    // Drive the handler directly, past the disabled attribute — the finding is
    // that `disabled` is the ONLY thing standing between a coverage-unresolved
    // order and the broker.
    confirmButton()!.click();
    await Promise.resolve();
    const placed = fetchSpy.mock.calls.filter(([url]) => String(url).includes("/api/orders/place"));
    expect(placed).toEqual([]);
  });
});
