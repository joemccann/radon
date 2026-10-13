/** @vitest-environment jsdom */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import PositionTab from "@/components/ticker-detail/PositionTab";
import type { OrdersData, PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
const realtime = vi.hoisted(() => ({ connected: true, ibConnected: true, marketDataDegraded: false }));
vi.mock("@/lib/RealtimePricesContext", () => ({ useRealtimePrices: () => realtime }));

const position = {
  id: 42, ticker: "CRWD", structure: "Combo (3 legs)", structure_type: "Combo", direction: "COMBO",
  contracts: 100, expiry: "2026-10-30", entry_date: "2026-10-01", entry_cost: -3162,
  legs: [
    { direction: "SHORT", type: "Call", strike: 270, contracts: 50, avg_cost: 1223.24, entry_cost: -61162 },
    { direction: "SHORT", type: "Put", strike: 230, contracts: 100, avg_cost: 119.69, entry_cost: -11969 },
    { direction: "LONG", type: "Put", strike: 260, contracts: 100, avg_cost: 699.69, entry_cost: 69969 },
  ],
} as PortfolioPosition;
const portfolio = { bankroll: 250000, positions: [position] } as PortfolioData;
function quote(symbol: string, bid: number, ask: number): PriceData {
  return { symbol, bid, ask, last: (bid + ask) / 2, lastIsCalculated: false, timestamp: new Date().toISOString() } as PriceData;
}
const prices = {
  CRWD_20261030_270_C: quote("CRWD_20261030_270_C", 8.6, 9),
  CRWD_20261030_230_P: quote("CRWD_20261030_230_P", 1.6, 1.8),
  CRWD_20261030_260_P: quote("CRWD_20261030_260_P", 9.6, 10),
};
afterEach(() => { cleanup(); vi.restoreAllMocks(); Object.assign(realtime, { connected: true, ibConnected: true, marketDataDegraded: false }); });
function openPuts() {
  render(<PositionTab position={position} prices={prices} portfolio={portfolio} orders={{ open_orders: [] } as unknown as OrdersData} />);
  fireEvent.click(screen.getByTestId("pos-leg-select-1"));
  fireEvent.click(screen.getByTestId("pos-leg-select-2"));
  fireEvent.click(screen.getByTestId("pos-trade-selected"));
}
describe("selected position legs", () => {
  it.each([
    { connected: false, ibConnected: true, marketDataDegraded: false },
    { connected: true, ibConnected: false, marketDataDegraded: false },
    { connected: true, ibConnected: true, marketDataDegraded: true },
  ])("cannot transmit with an unhealthy live feed: %j", async (state) => {
    Object.assign(realtime, state);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}"));
    openPuts();
    fireEvent.change(screen.getByTestId("position-trade-limit"), { target: { value: "8.1" } });
    fireEvent.click(screen.getByRole("button", { name: "Review Order" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "Confirm Order" }) as HTMLButtonElement).disabled).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Confirm Order" }));
    expect(fetchSpy.mock.calls.filter(([url]) => url === "/api/orders/place")).toHaveLength(0);
  });
  it("rechecks selected quote age at the wire after idle time in review", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}"));
    openPuts();
    fireEvent.change(screen.getByTestId("position-trade-limit"), { target: { value: "8.1" } });
    fireEvent.click(screen.getByRole("button", { name: "Review Order" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "Confirm Order" }) as HTMLButtonElement).disabled).toBe(false));
    const future = Date.now() + 6 * 60 * 1000;
    vi.spyOn(Date, "now").mockReturnValue(future);
    fireEvent.click(screen.getByRole("button", { name: "Confirm Order" }));
    expect(fetchSpy.mock.calls.filter(([url]) => url === "/api/orders/place")).toHaveLength(0);
  });
  it("disambiguates selection of the same strike across different expiries", () => {
    const calendar = { ...position, legs: [position.legs[0], { ...position.legs[0], expiry: "2026-11-20" }] };
    render(<PositionTab position={calendar} prices={prices} portfolio={portfolio} />);
    expect(screen.getByRole("checkbox", { name: "Select SHORT Call $270 expiring 2026-10-30" })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "Select SHORT Call $270 expiring 2026-11-20" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: /^Expiry/ })).toBeTruthy();
  });
  it("waits for working orders and disarms when a working cover reserves selected contracts", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}"));
    const view = render(<PositionTab position={position} prices={prices} portfolio={portfolio} orders={null} />);
    fireEvent.click(screen.getByTestId("pos-leg-select-1"));
    fireEvent.click(screen.getByTestId("pos-leg-select-2"));
    fireEvent.click(screen.getByTestId("pos-trade-selected"));
    fireEvent.change(screen.getByTestId("position-trade-limit"), { target: { value: "8.10" } });
    expect((screen.getByRole("button", { name: "Review Order" }) as HTMLButtonElement).disabled).toBe(true);
    view.rerender(<PositionTab position={position} prices={prices} portfolio={portfolio} orders={{ open_orders: [] } as unknown as OrdersData} />);
    fireEvent.click(screen.getByRole("button", { name: "Review Order" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "Confirm Order" }) as HTMLButtonElement).disabled).toBe(false));
    const orders = { open_orders: [{ status: "Submitted", action: "BUY", remaining: 50, totalQuantity: 50,
      contract: { symbol: "CRWD", secType: "OPT", expiry: "20261030", strike: 230, right: "P" } }] } as unknown as OrdersData;
    view.rerender(<PositionTab position={position} prices={prices} portfolio={portfolio} orders={orders} />);
    expect((screen.getByRole("button", { name: "Confirm Order" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Confirm Order" }));
    expect(fetchSpy.mock.calls.filter(([url]) => url === "/api/orders/place")).toHaveLength(0);
  });
  it("invalidates a selected ticket when the broker changes held quantities", () => {
    const view = render(<PositionTab position={position} prices={prices} portfolio={portfolio} orders={{ open_orders: [] } as unknown as OrdersData} />);
    fireEvent.click(screen.getByTestId("pos-leg-select-1"));
    fireEvent.click(screen.getByTestId("pos-trade-selected"));
    const updated = { ...position, legs: position.legs.map((leg, index) => index === 1 ? { ...leg, contracts: 90 } : leg) };
    view.rerender(<PositionTab position={updated} prices={prices} portfolio={portfolio} orders={{ open_orders: [] } as unknown as OrdersData} />);
    expect(screen.queryByTestId("position-trade-ticket")).toBeNull();
    expect((screen.getByTestId("pos-leg-select-1") as HTMLInputElement).checked).toBe(false);
    expect((screen.getByTestId("pos-trade-selected") as HTMLButtonElement).disabled).toBe(true);
  });
  it("selects the put spread independently, preserves selection through sorting and cancel", () => {
    render(<PositionTab position={position} prices={prices} portfolio={portfolio} orders={{ open_orders: [] } as unknown as OrdersData} />);
    expect((screen.getByTestId("pos-trade-selected") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("pos-leg-select-1"));
    fireEvent.click(screen.getByTestId("pos-leg-select-2"));
    fireEvent.click(screen.getByRole("columnheader", { name: /^Strike/ }));
    expect((screen.getByTestId("pos-leg-select-0") as HTMLInputElement).checked).toBe(false);
    expect((screen.getByTestId("pos-leg-select-1") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByTestId("pos-trade-selected"));
    expect((screen.getByTestId("position-trade-qty") as HTMLInputElement).value).toBe("100");
    expect(screen.getByTestId("position-trade-selected-legs").textContent).toContain("Buy to Close");
    expect(screen.getByTestId("position-trade-selected-legs").textContent).not.toContain("Call");
    expect(screen.getByTestId("position-trade-remaining-legs").textContent).toContain("SHORT 50x Call $270");
    fireEvent.click(screen.getByRole("button", { name: "Cancel trade" }));
    expect((screen.getByTestId("pos-leg-select-2") as HTMLInputElement).checked).toBe(true);
  });
  it("quotes only the selected spread using crossed bid/ask, and selected basis", async () => {
    openPuts();
    fireEvent.click(screen.getByRole("button", { name: /^BID/ }));
    expect((screen.getByTestId("position-trade-limit") as HTMLInputElement).value).toBe("7.80");
    fireEvent.click(screen.getByRole("button", { name: /^ASK/ }));
    expect((screen.getByTestId("position-trade-limit") as HTMLInputElement).value).toBe("8.40");
    fireEvent.click(screen.getByRole("button", { name: /^MID/ }));
    fireEvent.click(screen.getByRole("button", { name: "Review Order" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "Confirm Order" }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByTestId("position-trade-ticket").textContent).toContain("$23,000");
  });
  it("transmits only the two selected puts after review with the exact closing BAG", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ status: "ok" }), { status: 200 }));
    openPuts();
    fireEvent.change(screen.getByTestId("position-trade-limit"), { target: { value: "8.10" } });
    expect(fetchSpy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Review Order" }));
    expect(fetchSpy).not.toHaveBeenCalled();
    await waitFor(() => expect((screen.getByRole("button", { name: "Confirm Order" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Confirm Order" }));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/orders/place");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ type: "combo", symbol: "CRWD", action: "SELL", quantity: 100, limitPrice: 8.1, tif: "DAY", legs: [
      { expiry: "20261030", strike: 230, right: "P", action: "SELL", ratio: 1 },
      { expiry: "20261030", strike: 260, right: "P", action: "BUY", ratio: 1 },
    ] });
  });
  it("keeps an invalid or oversized quantity disarmed and cannot switch to opening", () => {
    openPuts();
    expect(within(screen.getByTestId("position-trade-ticket")).queryByRole("button", { name: "BUY" })).toBeNull();
    fireEvent.change(screen.getByTestId("position-trade-limit"), { target: { value: "8.10" } });
    for (const value of ["101", "1.5", "0", "-1"]) {
      fireEvent.change(screen.getByTestId("position-trade-qty"), { target: { value } });
      expect((screen.getByRole("button", { name: "Review Order" }) as HTMLButtonElement).disabled).toBe(true);
    }
  });
  it("blocks closing a protective wing alone without transmitting", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}"));
    render(<PositionTab position={position} prices={prices} portfolio={portfolio} orders={{ open_orders: [] } as unknown as OrdersData} />);
    fireEvent.click(screen.getByTestId("pos-leg-select-2"));
    fireEvent.click(screen.getByTestId("pos-trade-selected"));
    fireEvent.change(screen.getByTestId("position-trade-limit"), { target: { value: "9.8" } });
    fireEvent.click(screen.getByRole("button", { name: "Review Order" }));
    await waitFor(() => expect(screen.getByTestId("position-trade-ticket").textContent).toMatch(/uncover|retained/i));
    fireEvent.click(screen.getByRole("button", { name: "Confirm Order" }));
    expect(fetchSpy.mock.calls.filter(([url]) => url === "/api/orders/place")).toHaveLength(0);
  });
});
