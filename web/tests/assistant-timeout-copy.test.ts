import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

/**
 * 2026-08-29: the operator pasted a chart, asked how it related to their TLT
 * position, and the chat answered "Assistant service returned an error."
 * DevTools showed `POST /api/assistant -> 504` after tens of seconds: the edge
 * abandoned the turn, and the edge's 504 body is not JSON, so `payload?.error`
 * was null and the generic string was all the operator got.
 *
 * A turn the edge timed out has to say so, and say what might get through.
 */

const originalFetch = global.fetch;
const mockFetch = vi.fn();

beforeEach(() => {
  global.fetch = mockFetch as unknown as typeof fetch;
  mockFetch.mockReset();
});

afterEach(() => {
  global.fetch = originalFetch;
});

import { requestAssistantReply, requestAssistantTurn } from "../lib/chat";

/** What Caddy actually hands the browser: a status, and a body that is not JSON. */
function edgeTimeout(status = 504) {
  return {
    ok: false,
    status,
    text: async () => "",
  };
}

describe("assistant turn timeout copy", () => {
  it("names the timeout instead of the generic error on a 504", async () => {
    mockFetch.mockResolvedValue(edgeTimeout());

    const turn = await requestAssistantTurn([], "analyze this chart against my TLT position");

    expect(turn.content).not.toBe("Assistant service returned an error.");
    expect(turn.content).toMatch(/timed out/i);
    expect(turn.content).toMatch(/smaller image/i);
    expect(turn.content).toMatch(/shorter question/i);
    expect(turn.proposal).toBeNull();
    expect(turn.toolEvents).toEqual([]);
  });

  it("uses the same copy for a 408 request timeout", async () => {
    mockFetch.mockResolvedValue(edgeTimeout(408));

    const turn = await requestAssistantTurn([], "analyze this chart");

    expect(turn.content).toMatch(/timed out/i);
  });

  it("keeps the timeout copy when the timeout body does carry an error field", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 504,
      json: async () => ({ error: "upstream gone" }),
    });

    const turn = await requestAssistantTurn([], "analyze this chart");

    expect(turn.content).toMatch(/timed out/i);
  });

  it("leaves a non-timeout failure with its own reported error", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => ({ error: "Anthropic overloaded" }),
    });

    const turn = await requestAssistantTurn([], "analyze this chart");

    expect(turn.content).toBe("Error: Anthropic overloaded");
  });

  it("still falls back to the generic string for an unexplained non-timeout status", async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 500, json: async () => ({}) });

    const turn = await requestAssistantTurn([], "analyze this chart");

    expect(turn.content).toBe("Assistant service returned an error.");
  });

  it("applies the same copy to the plain reply path", async () => {
    mockFetch.mockResolvedValue(edgeTimeout());

    const reply = await requestAssistantReply([], "analyze this chart");

    expect(reply).toMatch(/timed out/i);
    expect(reply).toMatch(/smaller image/i);
  });

  it("uses no em dashes in operator-facing copy", async () => {
    mockFetch.mockResolvedValue(edgeTimeout());

    const turn = await requestAssistantTurn([], "analyze this chart");

    expect(turn.content).not.toContain("—");
  });
});
