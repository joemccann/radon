/**
 * @vitest-environment jsdom
 *
 * FU6 (A) — ChatPanel order-confirm card.
 *
 * The assistant route returns a structured order proposal for `place_order`:
 *   { tool, destructive, input, summary, toolUseId }
 *
 * ChatPanel must render it as a confirm card (summary + Confirm / Cancel) and:
 *   - NEVER auto-execute (no placement fetch until the operator clicks Confirm),
 *   - Confirm POSTs the proposed order to the placement path,
 *   - Cancel dismisses the card without placing anything.
 *
 * All network is mocked.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import ChatPanel from "@/components/ChatPanel";

afterEach(cleanup);

type FetchCall = { url: string; init?: RequestInit };

function mockFetchSequence(handlers: Record<string, () => unknown>) {
  const calls: FetchCall[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const key = Object.keys(handlers).find((k) => url.includes(k));
    const body = key ? handlers[key]() : { content: "ok" };
    return {
      ok: true,
      status: 200,
      json: async () => body,
    } as Response;
  });
  // @ts-expect-error test stub
  global.fetch = fetchMock;
  return { calls, fetchMock };
}

function mockFetchResponses(handlers: Record<string, () => Response>) {
  const calls: FetchCall[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const key = Object.keys(handlers).find((k) => url.includes(k));
    return key ? handlers[key]() : ({
      ok: true,
      status: 200,
      json: async () => ({ content: "ok" }),
    } as Response);
  });
  // @ts-expect-error test stub
  global.fetch = fetchMock;
  return { calls, fetchMock };
}

const PROPOSAL = {
  tool: "place_order",
  destructive: true as const,
  input: { type: "option" as const, ticker: "WULF", action: "BUY" as const, quantity: 10, limit_price: 5.6, expiry: "20260918", strike: 6, right: "C" as const, conId: 12345, exchange: "SMART" },
  summary: "BUY 10 WULF long call @ 5.6",
  toolUseId: "tu-1",
};

async function sendNonCommandMessage(text: string) {
  const textarea = screen.getByLabelText("Message Grok assistant");
  fireEvent.change(textarea, { target: { value: text } });
  const form = textarea.closest("form")!;
  fireEvent.submit(form);
}

describe("ChatPanel order-confirm card", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders a confirm card when the assistant returns a proposal", async () => {
    mockFetchSequence({
      "/api/assistant": () => ({ content: "Proposing an order.", proposal: PROPOSAL }),
    });

    render(<ChatPanel activeSection="dashboard" portfolio={{ positions: [] } as never} />);
    await sendNonCommandMessage("buy me some wulf calls");

    await waitFor(() => {
      expect(screen.getAllByText(PROPOSAL.summary)).toHaveLength(2);
    });
    expect(screen.getByRole("button", { name: /confirm/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /cancel/i })).toBeTruthy();
  });

  it("does NOT auto-execute the order (no placement call before Confirm)", async () => {
    const { calls } = mockFetchSequence({
      "/api/assistant": () => ({ content: "Proposing an order.", proposal: PROPOSAL }),
    });

    render(<ChatPanel activeSection="dashboard" portfolio={{ positions: [] } as never} />);
    await sendNonCommandMessage("buy me some wulf calls");

    await waitFor(() => {
      expect(screen.getAllByText(PROPOSAL.summary)).toHaveLength(2);
    });

    expect(calls.some((c) => c.url.includes("/api/orders/place"))).toBe(false);
  });

  it("Confirm POSTs the proposed order to the placement path", async () => {
    const { calls } = mockFetchSequence({
      "/api/assistant": () => ({ content: "Proposing an order.", proposal: PROPOSAL }),
      "/api/orders/place": () => ({ status: "ok", message: "Order placed" }),
    });

    render(<ChatPanel activeSection="dashboard" portfolio={{ positions: [] } as never} />);
    await sendNonCommandMessage("buy me some wulf calls");

    const confirm = await screen.findByRole("button", { name: /confirm/i });
    await waitFor(() => expect(confirm.hasAttribute("disabled")).toBe(false));
    fireEvent.click(confirm);

    await waitFor(() => {
      const placeCall = calls.find((c) => c.url.includes("/api/orders/place"));
      expect(placeCall).toBeTruthy();
    });

    const placeCall = calls.find((c) => c.url.includes("/api/orders/place"))!;
    expect(placeCall.init?.method).toBe("POST");
    const sentBody = JSON.parse(placeCall.init!.body as string);
    expect(sentBody.symbol).toBe("WULF");
    expect(sentBody.action).toBe("BUY");
    expect(sentBody.quantity).toBe(10);
    expect(sentBody.type).toBe("option");
    expect(sentBody.conId).toBe(12345);
    expect(sentBody.expiry).toBe("20260918");
    expect(sentBody.idempotencyKey).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("Cancel dismisses the card and places nothing", async () => {
    const { calls } = mockFetchSequence({
      "/api/assistant": () => ({ content: "Proposing an order.", proposal: PROPOSAL }),
    });

    render(<ChatPanel activeSection="dashboard" portfolio={{ positions: [] } as never} />);
    await sendNonCommandMessage("buy me some wulf calls");

    await waitFor(() => screen.getByRole("button", { name: /cancel/i }));
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    await waitFor(() => {
      expect(screen.queryByText(PROPOSAL.summary)).toBeNull();
    });
    expect(calls.some((c) => c.url.includes("/api/orders/place"))).toBe(false);
  });

  it("renders a controlled PI error when a PI command response is malformed", async () => {
    mockFetchResponses({
      "/api/pi": () => ({
        ok: false,
        status: 502,
        text: async () => "",
        json: async () => {
          throw new SyntaxError("Unexpected end of JSON input");
        },
      } as Response),
    });

    render(<ChatPanel activeSection="dashboard" portfolio={{ positions: [] } as never} />);
    await sendNonCommandMessage("/portfolio");

    await waitFor(() => {
      expect(screen.getByText(/PI command request failed/i)).toBeTruthy();
    });
    expect(screen.queryByText("No output.")).toBeNull();
    expect(screen.queryByText(/Unexpected end of JSON input/)).toBeNull();
  });

  it("updates the pending assistant row when a PI command request rejects", async () => {
    const calls: FetchCall[] = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      throw new Error("network down");
    });
    // @ts-expect-error test stub
    global.fetch = fetchMock;

    render(<ChatPanel activeSection="dashboard" portfolio={{ positions: [] } as never} />);
    await sendNonCommandMessage("/portfolio");

    await waitFor(() => {
      expect(screen.getByText(/PI command failed to run in this session/i)).toBeTruthy();
    });
    expect(screen.queryByText("No output.")).toBeNull();
    expect(screen.queryAllByText(/PI command failed to run in this session/i)).toHaveLength(1);
    expect(calls.some((c) => c.url.includes("/api/pi"))).toBe(true);
  });
});
