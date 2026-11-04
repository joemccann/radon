/** @vitest-environment jsdom */
//
// Listed contracts (futures, index options) get the same two-step gate as
// every other ticket: the first press reviews, a separate Transmit sends, an
// unbounded short needs an explicit acknowledgement, Enter in a field can
// never transmit, and the form resets when the contract changes so an old
// limit never goes out on a new contract.

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { PortfolioData } from "@/lib/types";

const mocks = vi.hoisted(() => ({ futuresState: null as unknown }));

vi.mock("@/lib/TickerDetailContext", () => ({ useTickerDetailOptional: () => null }));
vi.mock("@/lib/useFuturesChain", () => ({ useFuturesChain: () => mocks.futuresState }));

import { FuturesOrderForm } from "../components/ticker-detail/FuturesOrderForm";

const SEP = {
  conId: 9001,
  symbol: "VIX",
  localSymbol: "VIXU6",
  exchange: "CFE",
  currency: "USD",
  lastTradeDateOrContractMonth: "20260916",
  multiplier: "1000",
  tradingClass: "VX",
  marketName: "VIX",
  minTick: 0.05,
};
const OCT = { ...SEP, conId: 9002, localSymbol: "VIXV6", lastTradeDateOrContractMonth: "20261021" };

const PORTFOLIO = { positions: [] } as unknown as PortfolioData;

type SentRequest = { url: string; method: string; body: unknown };
const sent: SentRequest[] = [];

beforeEach(() => {
  sent.length = 0;
  mocks.futuresState = {
    data: { symbol: "VIX", exchange: "CFE", contracts: [SEP, OCT], count: 2 },
    loading: false,
    error: null,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      // Every write, recorded whole. The risk gate's own GETs (locate chip)
      // are not orders.
      const method = init?.method ?? "GET";
      if (method !== "GET") {
        sent.push({
          url: String(input),
          method,
          body: init?.body ? JSON.parse(String(init.body)) : null,
        });
      }
      return new Response(JSON.stringify({ orderId: 7, status: "Submitted" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const limitInput = () => screen.getByPlaceholderText("0.00") as HTMLInputElement;
const qtyInput = () => screen.getByPlaceholderText("Contracts") as HTMLInputElement;
const tifSelect = () => screen.getAllByRole("combobox")[1] as HTMLSelectElement;
const expirySelect = () => screen.getAllByRole("combobox")[0] as HTMLSelectElement;
const review = () => screen.queryByTestId("listed-order-review") as HTMLButtonElement | null;
const transmit = () => screen.queryByTestId("listed-order-transmit") as HTMLButtonElement | null;

async function stageShort() {
  render(<FuturesOrderForm ticker="VIX" portfolio={PORTFOLIO} />);
  fireEvent.click(screen.getByRole("button", { name: "SELL" }));
  fireEvent.change(limitInput(), { target: { value: "20" } });
  await waitFor(() => expect(review()?.disabled).toBe(false));
}

describe("listed-contract confirm gate", () => {
  it("the first press reviews, it does not send", async () => {
    await stageShort();
    fireEvent.click(review()!);
    expect(sent).toHaveLength(0);
    expect(transmit()).toBeTruthy();
  });

  it("Enter in a field never transmits, armed or not", async () => {
    await stageShort();
    const form = limitInput().closest("form")!;
    fireEvent.submit(form);
    expect(sent).toHaveLength(0);
    fireEvent.click(await screen.findByTestId("ticket-unbounded-ack"));
    await waitFor(() => expect(transmit()?.disabled).toBe(false));
    fireEvent.submit(form);
    expect(sent).toHaveLength(0);
  });

  it("a short future holds transmit until the unbounded loss is acknowledged", async () => {
    await stageShort();
    fireEvent.click(review()!);
    const ack = (await screen.findByTestId("ticket-unbounded-ack")) as HTMLInputElement;
    expect(ack.checked).toBe(false);
    expect(transmit()!.disabled).toBe(true);
    expect(screen.getByTestId("ticket-unbounded-warning").textContent?.toUpperCase()).toContain("UNBOUNDED");

    // Past `disabled`: the handler itself must refuse while unacknowledged.
    const key = Object.keys(transmit()!).find((k) => k.startsWith("__reactProps$"))!;
    const onClick = (transmit() as unknown as Record<string, { onClick: () => Promise<void> }>)[key].onClick;
    await act(async () => {
      await onClick();
    });
    expect(sent).toHaveLength(0);

    fireEvent.click(ack);
    await waitFor(() => expect(transmit()!.disabled).toBe(false));
    await act(async () => {
      fireEvent.click(transmit()!);
    });
    expect(sent).toEqual([
      {
        url: "/api/orders/place",
        method: "POST",
        body: {
          type: "future",
          symbol: "VIX",
          action: "SELL",
          quantity: 1,
          limitPrice: 20,
          tif: "DAY",
          conId: 9001,
          exchange: "CFE",
          multiplier: 1000,
        },
      },
    ]);
  });

  it("a bounded long needs no acknowledgement", async () => {
    render(<FuturesOrderForm ticker="VIX" portfolio={PORTFOLIO} />);
    fireEvent.change(limitInput(), { target: { value: "19.5" } });
    await waitFor(() => expect(review()?.disabled).toBe(false));
    fireEvent.click(review()!);
    expect(screen.queryByTestId("ticket-unbounded-ack")).toBeNull();
    await waitFor(() => expect(transmit()?.disabled).toBe(false));
    await act(async () => {
      fireEvent.click(transmit()!);
    });
    expect(sent).toHaveLength(1);
    expect(sent[0].url).toBe("/api/orders/place");
    expect(sent[0].method).toBe("POST");
    expect(sent[0].body).toMatchObject({ action: "BUY", limitPrice: 19.5, conId: 9001 });
  });

  it("editing the order while armed disarms", async () => {
    await stageShort();
    fireEvent.click(review()!);
    expect(transmit()).toBeTruthy();
    fireEvent.change(limitInput(), { target: { value: "21" } });
    expect(transmit()).toBeNull();
    expect(sent).toHaveLength(0);
  });

  it("changing the contract resets action, size, limit and TIF and disarms", async () => {
    await stageShort();
    fireEvent.change(qtyInput(), { target: { value: "3" } });
    fireEvent.change(tifSelect(), { target: { value: "GTC" } });
    await waitFor(() => expect(review()?.disabled).toBe(false));
    fireEvent.click(review()!);
    expect(transmit()).toBeTruthy();

    fireEvent.change(expirySelect(), { target: { value: String(OCT.conId) } });

    expect(transmit()).toBeNull();
    expect(limitInput().value).toBe("");
    expect(qtyInput().value).toBe("1");
    expect(tifSelect().value).toBe("DAY");
    expect(screen.getByRole("button", { name: "BUY" }).className).toContain("futures-form-action--active");
    expect(sent).toHaveLength(0);
  });
});
