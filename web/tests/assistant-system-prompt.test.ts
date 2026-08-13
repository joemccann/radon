import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * System-prompt conventions (2026-07-24 max-rounds incident):
 *   - JOURNAL CONVENTIONS section documents the two row families, the dedup
 *     rule, and cross-window VWAP lot-matching.
 *   - Trade-history / P&L questions are steered to the journal tools, not
 *     search_knowledge.
 *   - The per-request system string carries today's ET date (the date-window
 *     tools are useless without it); SYSTEM_PROMPT itself stays static.
 */

const ENV_KEYS = ["ASSISTANT_MOCK", "NODE_ENV", "ANTHROPIC_API_KEY"];

function postRequest(body: unknown): Request {
  return new Request("http://localhost/api/assistant", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("assistant system prompt", () => {
  const saved: Record<string, string | undefined> = {};

  beforeEach(() => {
    vi.resetModules();
    for (const key of ENV_KEYS) saved[key] = process.env[key];
    process.env.ASSISTANT_MOCK = "1";
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.doUnmock("@/lib/llm/provider");
    for (const key of ENV_KEYS) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  });

  it("documents journal conventions and steers P&L questions to the journal tools", async () => {
    const { SYSTEM_PROMPT } = await import("@/app/api/assistant/route");

    expect(SYSTEM_PROMPT).toContain("JOURNAL CONVENTIONS");
    expect(SYSTEM_PROMPT).toContain("flex_agg");
    expect(SYSTEM_PROMPT).toContain("get_realized_pnl");
    expect(SYSTEM_PROMPT).toContain("query_journal");
    expect(SYSTEM_PROMPT).toContain("go straight to the journal tools");
    // Tool-surface sentence mentions the journal alongside the live surfaces.
    expect(SYSTEM_PROMPT).toContain("the trade journal");
    expect(SYSTEM_PROMPT).toContain("get_quote");
    expect(SYSTEM_PROMPT).toContain("rank_spreads");
    expect(SYSTEM_PROMPT).toContain("run_evaluate");
    expect(SYSTEM_PROMPT).toContain("Never invent a spot price");
    // No em dashes in user-facing-adjacent copy.
    expect(SYSTEM_PROMPT).not.toContain("—");
    // The static prompt carries no baked-in date; the route injects it per request.
    expect(SYSTEM_PROMPT).not.toContain("Today is");
  });

  it("injects today's ET date into the per-request system string", async () => {
    const chat = vi.fn().mockResolvedValue({
      provider: "anthropic",
      model: "mock-model",
      text: "ok",
      stopReason: "end_turn",
    });
    vi.doMock("@/lib/llm/provider", () => ({ chat }));

    const { POST, SYSTEM_PROMPT } = await import("@/app/api/assistant/route");
    const res = await POST(postRequest({ messages: [{ role: "user", content: "hello" }] }) as never);
    expect(res.status).toBe(200);

    expect(chat).toHaveBeenCalledTimes(1);
    const request = chat.mock.calls[0][0] as { system?: string };
    expect(request.system).toBeDefined();
    expect(request.system!.startsWith(SYSTEM_PROMPT)).toBe(true);
    expect(request.system).toMatch(/Today is \d{4}-\d{2}-\d{2} \(America\/New_York\)\.$/);
  });
});
