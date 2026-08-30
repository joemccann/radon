import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { assistantDonePayload } from "./assistantStream";

/**
 * Item 5 — assistant telemetry.
 *
 * The 2026-07-24 max-rounds incident was undiagnosable: rounds/toolEvents/usage
 * existed in memory and were discarded. `recordAssistantTurn` persists one row
 * per /api/assistant turn to Turso `assistant_turns` through the dbExecute
 * chokepoint, fire-and-forget: it must never throw, never leak an unhandled
 * rejection, and never change the route's response. The route also emits one
 * `[assistant] done ...` console line per turn (per-round lines come from the
 * loop).
 */

const ENV_KEYS = ["ASSISTANT_MOCK", "NODE_ENV", "ANTHROPIC_API_KEY"];

function postRequest(body: unknown): Request {
  return new Request("http://localhost/api/assistant", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function flushMicrotasks(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

const baseRecord = {
  ts: "2026-07-24T12:00:00.000Z",
  userMsg: "What was my P&L this week?",
  rounds: 3,
  toolCalls: [{ name: "get_realized_pnl", ok: true }],
  usage: { inputTokens: 100, outputTokens: 50 },
  outcome: "answered",
};

describe("assistant telemetry", () => {
  const saved: Record<string, string | undefined> = {};

  beforeEach(() => {
    vi.resetModules();
    for (const key of ENV_KEYS) saved[key] = process.env[key];
    process.env.ASSISTANT_MOCK = "1";
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.doUnmock("@/lib/dbExecute");
    vi.doUnmock("@/lib/llm/provider");
    vi.doUnmock("@/lib/assistant/tools");
    for (const key of ENV_KEYS) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  });

  it("issues one assistant_turns INSERT with ordered args and truncates user_msg to 200 chars", async () => {
    const dbExecute = vi.fn().mockResolvedValue({ rows: [] });
    vi.doMock("@/lib/dbExecute", () => ({ dbExecute }));

    const { recordAssistantTurn } = await import("@/lib/assistant/telemetry");
    const longMsg = "x".repeat(500);
    recordAssistantTurn({ ...baseRecord, userMsg: longMsg });
    await flushMicrotasks();

    expect(dbExecute).toHaveBeenCalledTimes(1);
    const [stmt, opts] = dbExecute.mock.calls[0];
    expect(stmt.sql).toMatch(/INSERT INTO assistant_turns/);
    expect(stmt.args).toEqual([
      baseRecord.ts,
      "x".repeat(200),
      3,
      JSON.stringify(baseRecord.toolCalls),
      JSON.stringify(baseRecord.usage),
      "answered",
    ]);
    expect(opts).toMatchObject({ label: "assistant-turns" });
  });

  it("writes NULL usage when usage is absent", async () => {
    const dbExecute = vi.fn().mockResolvedValue({ rows: [] });
    vi.doMock("@/lib/dbExecute", () => ({ dbExecute }));

    const { recordAssistantTurn } = await import("@/lib/assistant/telemetry");
    recordAssistantTurn({ ...baseRecord, usage: undefined });
    await flushMicrotasks();

    expect(dbExecute).toHaveBeenCalledTimes(1);
    expect(dbExecute.mock.calls[0][0].args[4]).toBeNull();
  });

  it("neither throws nor leaks an unhandled rejection when the write fails", async () => {
    const dbExecute = vi.fn().mockRejectedValue(new Error("boom"));
    vi.doMock("@/lib/dbExecute", () => ({ dbExecute }));

    const { recordAssistantTurn } = await import("@/lib/assistant/telemetry");
    expect(() => recordAssistantTurn(baseRecord)).not.toThrow();
    // Vitest fails CI on unhandled rejections; flushing here surfaces one.
    await flushMicrotasks();
    expect(dbExecute).toHaveBeenCalledTimes(1);
  });

  it("persists the turn and logs round + done lines after a successful POST", async () => {
    const dbExecute = vi.fn().mockResolvedValue({ rows: [] });
    vi.doMock("@/lib/dbExecute", () => ({ dbExecute }));

    const chat = vi
      .fn()
      .mockResolvedValueOnce({
        provider: "anthropic",
        model: "mock",
        text: "Pulling flow.",
        toolCalls: [{ id: "tu_1", name: "get_flow", input: { ticker: "SPY" } }],
        stopReason: "tool_use",
        usage: { inputTokens: 10, outputTokens: 5 },
      })
      .mockResolvedValueOnce({
        provider: "anthropic",
        model: "mock",
        text: "SPY flow is bullish accumulation.",
        stopReason: "end_turn",
        usage: { inputTokens: 20, outputTokens: 8 },
      });
    vi.doMock("@/lib/llm/provider", () => ({ chat }));

    const executeTool = vi.fn(async () => ({ ok: true, data: { ticker: "SPY" } }));
    vi.doMock("@/lib/assistant/tools", async () => {
      const actual = await vi.importActual<typeof import("@/lib/assistant/tools")>("@/lib/assistant/tools");
      return { ...actual, executeTool };
    });

    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});

    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(postRequest({ messages: [{ role: "user", content: "How is SPY flow?" }] }) as never);

    expect(res.status).toBe(200);
    const body = await assistantDonePayload<{ content: string; rounds: number }>(res);
    expect(body.content).toBe("SPY flow is bullish accumulation.");

    await vi.waitFor(() => expect(dbExecute).toHaveBeenCalledTimes(1));
    const [stmt] = dbExecute.mock.calls[0];
    expect(stmt.sql).toMatch(/INSERT INTO assistant_turns/);
    expect(stmt.args[1]).toBe("How is SPY flow?");
    expect(stmt.args[2]).toBe(2);
    expect(JSON.parse(stmt.args[3])).toEqual([{ name: "get_flow", ok: true }]);
    expect(JSON.parse(stmt.args[4])).toEqual({ inputTokens: 30, outputTokens: 13 });
    expect(stmt.args[5]).toBe("answered");

    const lines = logSpy.mock.calls.map((call) => call.join(" "));
    expect(lines.filter((line) => /^\[assistant\] round=\d+ /.test(line))).toHaveLength(2);
    expect(lines.some((line) => /^\[assistant\] done rounds=2 outcome=answered toolCalls=1 usageIn=30 usageOut=13 ms=\d+$/.test(line))).toBe(true);
  });

  it("POST stays 200 with an identical body when the telemetry write rejects", async () => {
    const dbExecute = vi.fn().mockRejectedValue(new Error("turso down"));
    vi.doMock("@/lib/dbExecute", () => ({ dbExecute }));

    const chat = vi.fn().mockResolvedValueOnce({
      provider: "anthropic",
      model: "mock",
      text: "SPY flow is bullish accumulation.",
      stopReason: "end_turn",
      usage: { inputTokens: 12, outputTokens: 4 },
    });
    vi.doMock("@/lib/llm/provider", () => ({ chat }));

    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});

    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(postRequest({ messages: [{ role: "user", content: "How is SPY flow?" }] }) as never);

    expect(res.status).toBe(200);
    const body = await assistantDonePayload<{ content: string; rounds: number }>(res);
    expect(body.content).toBe("SPY flow is bullish accumulation.");
    expect(body.rounds).toBe(1);

    await vi.waitFor(() => expect(dbExecute).toHaveBeenCalledTimes(1));
    await flushMicrotasks();
  });
});
