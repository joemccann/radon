import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Image content blocks across the provider layer and the agent loop.
 *
 * Anthropic takes the block verbatim (its native Messages API shape). The
 * OpenAI-compatible path (xAI Grok / OpenAI) has to translate it to
 * `image_url` parts, without regressing the cache-friendly plain-string
 * content that text-only turns still serialize to.
 */

type FetchCall = { url: string; init: RequestInit };

function captureFetch(responder: (call: FetchCall) => Response | Promise<Response>) {
  const calls: FetchCall[] = [];
  const impl = vi.fn(async (url: unknown, init?: RequestInit) => {
    const call: FetchCall = { url: String(url), init: init ?? {} };
    calls.push(call);
    return responder(call);
  });
  vi.stubGlobal("fetch", impl as unknown as typeof fetch);
  return { calls };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function bodyOf(call: FetchCall): Record<string, unknown> {
  return JSON.parse(String(call.init.body)) as Record<string, unknown>;
}

const IMAGE_BLOCK = {
  type: "image" as const,
  source: { type: "base64" as const, media_type: "image/png", data: "AAA" },
};

const ENV_KEYS = [
  "ASSISTANT_MOCK",
  "LLM_PROVIDER",
  "LLM_FALLBACK_PROVIDER",
  "ANTHROPIC_API_KEY",
  "CLAUDE_CODE_API_KEY",
  "CLAUDE_CODE_OAUTH_TOKEN",
  "CLAUDE_CONFIG_DIR",
  "CODEX_HOME",
  "GROK_AUTH_FILE",
  "XAI_OAUTH_TOKEN",
  "RADON_LADDER_ALLOW_PREPAID",
  "CLAUDE_API_KEY",
  "ANTHROPIC_MODEL",
  "OPENAI_API_KEY",
  "OPENAI_BASE_URL",
  "OPENAI_MODEL",
  "XAI_API_KEY",
  "GROK_API_KEY",
  "XAI_BASE_URL",
  "XAI_MODEL",
];

describe("llm provider image blocks", () => {
  afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });
  it("preserves images and native tool history on the shared service wire", async () => {
    vi.stubEnv("ASSISTANT_MOCK", "0");
    const { chat } = await import("@/lib/llm/provider");
    const { calls } = captureFetch(() => jsonResponse({ provider: "anthropic", model: "capable", text: "ok" }));
    const messages = [
      { role: "user" as const, content: [{ type: "text" as const, text: "Read" }, IMAGE_BLOCK] },
      { role: "assistant" as const, content: [{ type: "tool_use" as const, id: "tool-1", name: "read", input: {} }] },
      { role: "user" as const, content: [{ type: "tool_result" as const, tool_use_id: "tool-1", content: "42" }] },
    ];
    await chat({ messages });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toMatch(/model-gateway\/chat$/);
    expect(bodyOf(calls[0]).messages).toEqual(messages);
  });
});

describe("assistant loop image passthrough", () => {
  const saved: Record<string, string | undefined> = {};

  beforeEach(() => {
    vi.resetModules();
    for (const key of ["ASSISTANT_MOCK"]) saved[key] = process.env[key];
    process.env.ASSISTANT_MOCK = "1";
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.doUnmock("@/lib/llm/provider");
    for (const key of ["ASSISTANT_MOCK"]) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  });

  it("hands the first-turn image message to chat() intact", async () => {
    const chat = vi.fn().mockResolvedValue({
      provider: "anthropic",
      model: "mock",
      text: "That is a vol surface.",
      stopReason: "end_turn",
    });
    vi.doMock("@/lib/llm/provider", () => ({ chat }));

    const { runAssistantLoop } = await import("@/lib/assistant/loop");
    const result = await runAssistantLoop(
      [{ role: "user", content: [{ type: "text", text: "What is this?" }, IMAGE_BLOCK] }],
      "system",
      { userId: "user_1" },
    );

    expect(result.content).toBe("That is a vol surface.");
    expect(chat).toHaveBeenCalledTimes(1);
    expect(chat.mock.calls[0][0].messages).toEqual([
      {
        role: "user",
        content: [
          { type: "text", text: "What is this?" },
          { type: "image", source: { type: "base64", media_type: "image/png", data: "AAA" } },
        ],
      },
    ]);
  });
});
