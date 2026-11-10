// @vitest-environment jsdom
//
// The chain ticket arms for the REVIEWED payload, not for "the confirm step".
// Live quotes keep moving while the operator reads the review, the parent
// chain can add or flip legs under it, and TIF sits on the same rail. None of
// that may reach the wire as an order the operator did not review.

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

let searchParamsString = "";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(searchParamsString),
  usePathname: () => "/MU",
  useRouter: () => ({
    replace: vi.fn(), push: vi.fn(), prefetch: vi.fn(),
    back: vi.fn(), forward: vi.fn(), refresh: vi.fn(),
  }),
}));

vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ isWatched: () => false, toggleWatch: vi.fn() }),
}));

import TickerDetailContent from "../components/TickerDetailContent";
import { TickerDetailProvider } from "../lib/TickerDetailContext";
import { OrderActionsProvider } from "../lib/OrderActionsContext";
import type { PriceData } from "../lib/pricesProtocol";
import {
  CALL_970_KEY,
  EXPIRY,
  ORDERS,
  PORTFOLIO,
  PRICES,
  chainFetch,
  clickCallCell,
  findStrikeRow,
  optionQuote,
} from "./helpers/chainHarness";

type SentRequest = { url: string; method: string; body: string };
const sent: SentRequest[] = [];
const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  searchParamsString = "";
  sent.length = 0;
  fetchMock.mockReset();
  fetchMock.mockImplementation((input, init) => {
    const method = (init as RequestInit | undefined)?.method ?? "GET";
    if (method === "POST") {
      sent.push({ url: String(input), method, body: String((init as RequestInit).body) });
      return Promise.resolve(
        new Response(JSON.stringify({ orderId: 1, status: "Submitted" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }
    return chainFetch(input as RequestInfo | URL);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function tree(prices: Record<string, PriceData>) {
  return (
    <OrderActionsProvider>
      <TickerDetailProvider>
        <TickerDetailContent
          ticker="MU"
          activeTab="c"
          onTabChange={vi.fn()}
          prices={prices}
          fundamentals={{}}
          portfolio={PORTFOLIO}
          orders={ORDERS}
          theme="dark"
        />
      </TickerDetailProvider>
    </OrderActionsProvider>
  );
}

async function mountChain() {
  const utils = render(tree(PRICES));
  const expirySelect = (await screen.findAllByRole("combobox"))[0] as HTMLSelectElement;
  await waitFor(() => expect(expirySelect.value).toBe(EXPIRY.compact));
  await waitFor(() => findStrikeRow(970));
  return utils;
}

function builder(): HTMLElement {
  const el = document.querySelector(".order-builder");
  if (!el) throw new Error("order builder not rendered");
  return el as HTMLElement;
}

function limitInput(): HTMLInputElement {
  return builder().querySelector(".modify-price-input") as HTMLInputElement;
}

function tifButton(label: "DAY" | "GTC"): HTMLButtonElement {
  const btn = Array.from(builder().querySelectorAll<HTMLButtonElement>(".order-builder-tif-btn")).find(
    (b) => b.textContent === label,
  );
  if (!btn) throw new Error(`no ${label} TIF button`);
  return btn;
}

const transmit = () => screen.queryByTestId("ticket-transmit") as HTMLButtonElement | null;

/** Short 970 call, limit left on auto-mid (12.10 x 12.90 -> 12.50). */
async function armShortCallOnAutoMid() {
  const utils = await mountChain();
  clickCallCell(970, 0);
  await waitFor(() => expect(limitInput().value).toBe("12.50"));
  await waitFor(() => expect(builder().querySelector(".ticket-risk")).toBeTruthy());
  fireEvent.click(screen.getByTestId("ticket-verify"));
  fireEvent.click(await screen.findByTestId("ticket-unbounded-ack"));
  await waitFor(() => expect(transmit()?.disabled).toBe(false));
  return utils;
}

const MOVED_PRICES: Record<string, PriceData> = {
  ...PRICES,
  [CALL_970_KEY]: optionQuote(CALL_970_KEY, 13.1, 13.9),
};

describe("chain ticket arms for the reviewed payload", () => {
  it("a quote tick while armed neither rewrites the reviewed price nor drops the acknowledgement", async () => {
    const { rerender } = await armShortCallOnAutoMid();

    rerender(tree(MOVED_PRICES));
    // The live mid is now 13.50; the reviewed price was 12.50.
    await waitFor(() =>
      expect(builder().textContent).toContain("13.50"),
    );
    expect(limitInput().value).toBe("12.50");
    expect((screen.getByTestId("ticket-unbounded-ack") as HTMLInputElement).checked).toBe(true);
    expect(transmit()?.disabled).toBe(false);

    await act(async () => {
      fireEvent.click(transmit()!);
    });
    expect(sent).toHaveLength(1);
    expect(sent[0].url).toBe("/api/orders/place");
    expect(sent[0].method).toBe("POST");
    const body = JSON.parse(sent[0].body);
    expect(body.limitPrice).toBe(12.5);
    expect(body.action).toBe("SELL");
    expect(body.strike).toBe(970);
  });

  it("flipping a staged leg from the chain disarms", async () => {
    await armShortCallOnAutoMid();
    // Ask cell on the same strike: BUY, which flips the staged SELL leg.
    clickCallCell(970, 2);
    await waitFor(() => expect(transmit()).toBeNull());
    expect(screen.getByTestId("ticket-verify")).toBeTruthy();
    expect(sent).toHaveLength(0);
  });

  it("adding a leg from the chain disarms", async () => {
    await armShortCallOnAutoMid();
    clickCallCell(960, 2);
    await waitFor(() => expect(transmit()).toBeNull());
    expect(sent).toHaveLength(0);
  });

  it("changing TIF while armed disarms", async () => {
    await armShortCallOnAutoMid();
    fireEvent.click(tifButton("GTC"));
    await waitFor(() => expect(transmit()).toBeNull());
    expect(sent).toHaveLength(0);
  });

  it("re-verifying after a change asks for the acknowledgement again", async () => {
    await armShortCallOnAutoMid();
    fireEvent.click(tifButton("GTC"));
    await waitFor(() => expect(transmit()).toBeNull());
    fireEvent.click(screen.getByTestId("ticket-verify"));
    const ack = (await screen.findByTestId("ticket-unbounded-ack")) as HTMLInputElement;
    expect(ack.checked).toBe(false);
    expect(transmit()?.disabled).toBe(true);
  });
});
