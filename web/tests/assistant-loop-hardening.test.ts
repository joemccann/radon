import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Loop hardening (2026-07-24 max-rounds incident):
 *   (a) Cap-hit forced final: after MAX_ROUNDS the loop makes ONE tool-less
 *       chat call asking the model to answer with what it has; the canned
 *       "Reached the maximum tool-calling rounds" string survives only when
 *       that final call itself fails.
 *   (b) Repeated-call short-circuit: an identical tool name+args (stable
 *       stringify, key order irrelevant) returns the prior result with a
 *       nudge instead of re-executing the tool.
 */

const CANNED_CAP_MESSAGE = "Reached the maximum tool-calling rounds without a final answer.";
const PRINCIPAL = { userId: "user_test", token: "jwt-token" };

type MockChatResponse = {
  provider: string;
  model: string;
  text: string;
  toolCalls?: Array<{ id: string; name: string; input: Record<string, unknown> }>;
  usage?: { inputTokens: number; outputTokens: number };
  stopReason?: string;
};

function toolUseResponse(round: number, input: Record<string, unknown>): MockChatResponse {
  return {
    provider: "anthropic",
    model: "mock-model",
    text: "",
    toolCalls: [{ id: `tu_${round}`, name: "get_portfolio", input }],
    usage: { inputTokens: 10, outputTokens: 5 },
    stopReason: "tool_use",
  };
}

type ChatMock = ReturnType<typeof vi.fn>;
type ExecuteToolMock = ReturnType<typeof vi.fn>;

async function loadLoop(chat: ChatMock, executeTool: ExecuteToolMock) {
  vi.doMock("@/lib/llm/provider", () => ({ chat }));
  vi.doMock("@/lib/assistant/tools", async () => {
    const actual = await vi.importActual<typeof import("@/lib/assistant/tools")>("@/lib/assistant/tools");
    return { ...actual, executeTool };
  });
  const { runAssistantLoop } = await import("@/lib/assistant/loop");
  return runAssistantLoop;
}

const okExecuteTool = () =>
  vi.fn(async (_name: string, _input: Record<string, unknown>, _token?: string) => ({
    ok: true,
    data: { hits: 1 },
  }));

describe("assistant loop hardening", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.spyOn(console, "log").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.doUnmock("@/lib/llm/provider");
    vi.doUnmock("@/lib/assistant/tools");
  });

  it("cap-hit makes one forced tool-less final call and returns its text", async () => {
    const chat = vi.fn();
    for (let round = 1; round <= 6; round += 1) {
      chat.mockResolvedValueOnce(toolUseResponse(round, { query: `q${round}` }));
    }
    chat.mockResolvedValueOnce({
      provider: "anthropic",
      model: "mock-model",
      text: "Partial: could not determine X.",
      usage: { inputTokens: 10, outputTokens: 5 },
      stopReason: "end_turn",
    });

    const executeTool = okExecuteTool();
    const runAssistantLoop = await loadLoop(chat, executeTool);
    const result = await runAssistantLoop([{ role: "user", content: "Weekly P&L?" }], "system prompt", PRINCIPAL);

    expect(chat).toHaveBeenCalledTimes(7);

    const finalRequest = chat.mock.calls[6][0] as {
      tools?: unknown;
      messages: Array<{ role: string; content: unknown }>;
    };
    expect(finalRequest.tools).toBeUndefined();
    const lastMessage = finalRequest.messages[finalRequest.messages.length - 1];
    expect(lastMessage.role).toBe("user");
    expect(String(lastMessage.content)).toContain("tool-call limit");

    expect(result.content).toBe("Partial: could not determine X.");
    expect(result.rounds).toBe(7);
    expect(result.outcome).toBe("cap_forced_final");
    expect(result.usage).toEqual({ inputTokens: 70, outputTokens: 35 });
  });

  it("cap-hit falls back to the canned string only when the forced final call fails", async () => {
    const chat = vi.fn();
    for (let round = 1; round <= 6; round += 1) {
      chat.mockResolvedValueOnce(toolUseResponse(round, { query: `q${round}` }));
    }
    chat.mockRejectedValueOnce(new Error("provider down"));

    const runAssistantLoop = await loadLoop(chat, okExecuteTool());
    const result = await runAssistantLoop([{ role: "user", content: "Weekly P&L?" }], "system prompt", PRINCIPAL);

    expect(chat).toHaveBeenCalledTimes(7);
    expect(result.content).toBe(CANNED_CAP_MESSAGE);
    expect(result.outcome).toBe("cap_fallback");
    expect(result.rounds).toBe(6);
  });

  it("short-circuits an identical repeated call (key order irrelevant) without re-executing the tool", async () => {
    const chat = vi
      .fn()
      .mockResolvedValueOnce(toolUseResponse(1, { query: "weekly pnl", sources: ["journal"] }))
      .mockResolvedValueOnce(toolUseResponse(2, { sources: ["journal"], query: "weekly pnl" }))
      .mockResolvedValueOnce({
        provider: "anthropic",
        model: "mock-model",
        text: "Done.",
        usage: { inputTokens: 10, outputTokens: 5 },
        stopReason: "end_turn",
      });

    const executeTool = okExecuteTool();
    const runAssistantLoop = await loadLoop(chat, executeTool);
    const result = await runAssistantLoop([{ role: "user", content: "Weekly P&L?" }], "system prompt", PRINCIPAL);

    expect(executeTool).toHaveBeenCalledTimes(1);
    expect(result.toolEvents).toHaveLength(2);
    expect(result.toolEvents[0].repeated).toBeUndefined();
    expect(result.toolEvents[1].repeated).toBe(true);
    expect(result.outcome).toBe("answered");
    expect(result.content).toBe("Done.");

    // Round 3's request carries round 2's tool_result: the repeated-call nudge
    // plus the earlier result body.
    const thirdRequest = chat.mock.calls[2][0] as {
      messages: Array<{ role: string; content: Array<{ type: string; content?: string }> | string }>;
    };
    const lastMessage = thirdRequest.messages[thirdRequest.messages.length - 1];
    const blocks = lastMessage.content as Array<{ type: string; content: string }>;
    expect(blocks[0].type).toBe("tool_result");
    expect(blocks[0].content.startsWith("REPEATED CALL")).toBe(true);
    expect(blocks[0].content).toContain(JSON.stringify({ hits: 1 }));
  });

  it("still executes a same-tool call whose arguments differ", async () => {
    const chat = vi
      .fn()
      .mockResolvedValueOnce(toolUseResponse(1, { query: "weekly pnl" }))
      .mockResolvedValueOnce(toolUseResponse(2, { query: "sndk close" }))
      .mockResolvedValueOnce({
        provider: "anthropic",
        model: "mock-model",
        text: "Done.",
        stopReason: "end_turn",
      });

    const executeTool = okExecuteTool();
    const runAssistantLoop = await loadLoop(chat, executeTool);
    const result = await runAssistantLoop([{ role: "user", content: "Weekly P&L?" }], "system prompt", PRINCIPAL);

    expect(executeTool).toHaveBeenCalledTimes(2);
    expect(result.toolEvents.every((event) => event.repeated !== true)).toBe(true);
  });

  it("destructive_call_waits_for_required_reads_and_runtime_validation", async () => {
    const validOrder = {
      type: "stock", ticker: "AAPL", action: "BUY", quantity: 1, limit_price: 200,
    };
    const chat = vi.fn()
      .mockResolvedValueOnce({
        provider: "anthropic", model: "mock-model", text: "",
        toolCalls: [
          { id: "read", name: "get_portfolio", input: {} },
          { id: "order-early", name: "place_order", input: validOrder },
        ],
      })
      .mockResolvedValueOnce({
        provider: "anthropic", model: "mock-model", text: "Reviewed.",
        toolCalls: [{ id: "order-final", name: "place_order", input: validOrder }],
      });
    const executeTool = okExecuteTool();
    const runAssistantLoop = await loadLoop(chat, executeTool);

    const result = await runAssistantLoop(
      [{ role: "user", content: "Buy 1 AAPL and place the order after checking my portfolio" }],
      "system prompt",
      PRINCIPAL,
    );

    expect(executeTool).toHaveBeenCalledTimes(1);
    expect(executeTool).toHaveBeenCalledWith("get_portfolio", {}, PRINCIPAL);
    expect(result.outcome).toBe("proposal");
    expect(result.proposal?.toolUseId).toBe("order-final");
  });

  it("logs one console line per round with the requested tool names", async () => {
    const logSpy = vi.mocked(console.log);
    const chat = vi
      .fn()
      .mockResolvedValueOnce(toolUseResponse(1, { query: "weekly pnl" }))
      .mockResolvedValueOnce({
        provider: "anthropic",
        model: "mock-model",
        text: "Done.",
        stopReason: "end_turn",
      });

    const runAssistantLoop = await loadLoop(chat, okExecuteTool());
    await runAssistantLoop([{ role: "user", content: "Weekly P&L?" }], "system prompt", PRINCIPAL);

    const lines = logSpy.mock.calls.map((call) => String(call[0]));
    expect(lines.some((line) => /^\[assistant\] round=1 .*tools=get_portfolio/.test(line))).toBe(true);
    expect(lines.some((line) => /^\[assistant\] round=2 .*tools=none/.test(line))).toBe(true);
  });
});
