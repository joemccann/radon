/**
 * Audit 2026-11-03 §3.5 "WS-ticket backoff singleton": the shared ticket block
 * holds BOTH sockets (prices + headlines). Only failures that say the ticket
 * service itself is struggling may raise it: transport errors, our own
 * request timeout, 5xx and 429. A caller abort, or a 401/403 (this tab's Clerk
 * token, fixed by the next attempt's fresh token), must not stall the other
 * socket.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  classifyTicketFailure,
  getWsTicket,
  resetTicketBackoffForTests,
  ticketBlockedMs,
} from "@/lib/wsTicket";

const fetchMock = vi.fn();

beforeEach(() => {
  resetTicketBackoffForTests();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  resetTicketBackoffForTests();
});

const status = (code: number) => new Response("{}", { status: code });

describe("shared ws-ticket backoff", () => {
  it.each([401, 403])("HTTP %i does not block either socket", async (code) => {
    fetchMock.mockResolvedValue(status(code));
    await expect(getWsTicket("t")).rejects.toThrow(String(code));
    expect(ticketBlockedMs()).toBe(0);
  });

  it("a caller abort does not block either socket", async () => {
    const controller = new AbortController();
    fetchMock.mockImplementation((_url: string, init: RequestInit) => new Promise((_, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    const pending = getWsTicket("t", controller.signal);
    controller.abort();
    await expect(pending).rejects.toThrow();
    expect(ticketBlockedMs()).toBe(0);
  });

  it.each([500, 502, 503, 429])("HTTP %i backs both sockets off", async (code) => {
    fetchMock.mockResolvedValue(status(code));
    await expect(getWsTicket("t")).rejects.toThrow(String(code));
    expect(ticketBlockedMs()).toBeGreaterThan(0);
  });

  it("a network failure backs both sockets off", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(getWsTicket("t")).rejects.toThrow("Failed to fetch");
    expect(ticketBlockedMs()).toBeGreaterThan(0);
  });

  it("a success clears a prior block", async () => {
    fetchMock.mockResolvedValueOnce(status(503));
    await expect(getWsTicket("t")).rejects.toThrow();
    resetTicketBackoffForTests();
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ ticket: "ok" }), { status: 200 }));
    await expect(getWsTicket("t")).resolves.toBe("ok");
    expect(ticketBlockedMs()).toBe(0);
  });
});

describe("classifyTicketFailure", () => {
  it("classifies the failure kinds", () => {
    expect(classifyTicketFailure({ status: 401 })).toBe("auth");
    expect(classifyTicketFailure({ status: 403 })).toBe("auth");
    expect(classifyTicketFailure({ status: 404 })).toBe("client");
    expect(classifyTicketFailure({ status: 429 })).toBe("throttled");
    expect(classifyTicketFailure({ status: 503 })).toBe("server");
    expect(classifyTicketFailure({ error: new TypeError("x"), callerAborted: false })).toBe("transport");
    expect(classifyTicketFailure({ error: new DOMException("t", "TimeoutError"), callerAborted: false })).toBe("transport");
    expect(classifyTicketFailure({ error: new DOMException("a", "AbortError"), callerAborted: true })).toBe("aborted");
  });
});
