import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * F7 — Agentic tool-calling assistant loop.
 *
 * The route drives a multi-round loop over F2's `chat()`:
 *   model -> tool_use blocks -> execute READ tools via radonFetch -> tool_result
 *   -> model again -> ... -> final text.
 *
 * Destructive tools (place_order) are NEVER auto-executed: the loop short-circuits
 * and returns a structured confirm proposal the ChatPanel renders, mirroring the
 * OrderRiskGate confirm discipline.
 *
 * Everything stays offline: the model is stubbed via the provider, and tool
 * execution is mocked. We assert global fetch is never touched.
 */

const ENV_KEYS = ["ASSISTANT_MOCK", "NODE_ENV", "ANTHROPIC_API_KEY"];

function postRequest(body: unknown): Request {
  return new Request("http://localhost/api/assistant", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("assistant tool-calling loop", () => {
  const saved: Record<string, string | undefined> = {};

  beforeEach(() => {
    vi.resetModules();
    for (const key of ENV_KEYS) saved[key] = process.env[key];
    process.env.ASSISTANT_MOCK = "1";
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.doUnmock("@/lib/llm/provider");
    vi.doUnmock("@/lib/assistant/tools");
    for (const key of ENV_KEYS) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  });

  it("declares READ tools and flags place_order destructive", async () => {
    const { ASSISTANT_TOOLS, isDestructiveTool } = await import("@/lib/assistant/tools");

    const names = ASSISTANT_TOOLS.map((tool) => tool.name);
    expect(names).toEqual(expect.arrayContaining(["get_flow", "run_scan", "get_gex", "get_portfolio", "place_order"]));

    for (const tool of ASSISTANT_TOOLS) {
      expect(tool).toHaveProperty("input_schema");
      expect(tool.input_schema).toMatchObject({ type: "object" });
    }

    expect(isDestructiveTool("place_order")).toBe(true);
    expect(isDestructiveTool("get_flow")).toBe(false);
  });

  it("runs a read tool round-trip and returns final text without live network", async () => {
    const fetchSpy = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy as unknown as typeof fetch);

    // Model: round 1 asks for get_flow; round 2 (after tool_result) answers.
    const chat = vi
      .fn()
      .mockResolvedValueOnce({
        provider: "anthropic",
        model: "mock",
        text: "Let me pull the flow.",
        toolCalls: [{ id: "tu_1", name: "get_flow", input: { ticker: "SPY" } }],
        stopReason: "tool_use",
      })
      .mockResolvedValueOnce({
        provider: "anthropic",
        model: "mock",
        text: "SPY flow is bullish accumulation.",
        stopReason: "end_turn",
      });
    vi.doMock("@/lib/llm/provider", () => ({ chat }));

    const executeTool = vi.fn(
      async (_name: string, _input: Record<string, unknown>, _token?: string) => ({
        ok: true,
        data: { ticker: "SPY", net_premium: 1_000_000 },
      }),
    );
    vi.doMock("@/lib/assistant/tools", async () => {
      const actual = await vi.importActual<typeof import("@/lib/assistant/tools")>("@/lib/assistant/tools");
      return { ...actual, executeTool };
    });

    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(postRequest({ messages: [{ role: "user", content: "How is SPY flow?" }] }) as never);

    expect(res.status).toBe(200);
    const body = await res.json();

    expect(chat).toHaveBeenCalledTimes(2);
    expect(executeTool).toHaveBeenCalledTimes(1);
    expect(executeTool.mock.calls[0][0]).toBe("get_flow");
    expect(executeTool.mock.calls[0][1]).toEqual({ ticker: "SPY" });

    expect(body.content).toBe("SPY flow is bullish accumulation.");
    expect(body.toolEvents).toEqual([
      expect.objectContaining({ name: "get_flow", input: { ticker: "SPY" } }),
    ]);
    expect(body.proposal).toBeFalsy();

    // The loop must never reach out over the network itself.
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("returns a confirm proposal for place_order instead of executing it", async () => {
    const fetchSpy = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy as unknown as typeof fetch);

    const chat = vi.fn().mockResolvedValueOnce({
      provider: "anthropic",
      model: "mock",
      text: "I'll prepare that order for your confirmation.",
      toolCalls: [
        {
          id: "tu_order",
          name: "place_order",
          input: {
            type: "option",
            ticker: "SPY",
            action: "BUY",
            quantity: 1,
            limit_price: 5.0,
            expiry: "20260918",
            strike: 500,
            right: "C",
            conId: 12345,
            exchange: "SMART",
          },
        },
      ],
      stopReason: "tool_use",
    });
    vi.doMock("@/lib/llm/provider", () => ({ chat }));

    const executeTool = vi.fn(async (_name: string, _input: Record<string, unknown>, _token?: string) => ({ ok: true }));
    vi.doMock("@/lib/assistant/tools", async () => {
      const actual = await vi.importActual<typeof import("@/lib/assistant/tools")>("@/lib/assistant/tools");
      return { ...actual, executeTool };
    });

    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(postRequest({ messages: [{ role: "user", content: "Buy 1 SPY call" }] }) as never);

    expect(res.status).toBe(200);
    const body = await res.json();

    // Destructive tool must NOT be auto-executed.
    expect(executeTool).not.toHaveBeenCalled();
    // The model is called exactly once; the loop halts pending user confirmation.
    expect(chat).toHaveBeenCalledTimes(1);

    expect(body.proposal).toMatchObject({
      tool: "place_order",
      destructive: true,
      input: {
        type: "option",
        ticker: "SPY",
        action: "BUY",
        quantity: 1,
        limit_price: 5.0,
        expiry: "20260918",
        strike: 500,
        right: "C",
        conId: 12345,
        exchange: "SMART",
      },
    });
    expect(typeof body.proposal.summary).toBe("string");
    expect(body.proposal.summary.length).toBeGreaterThan(0);

    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
