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
  const saved: Record<string, string | undefined> = {};

  beforeEach(() => {
    for (const key of ENV_KEYS) saved[key] = process.env[key];
    for (const key of ENV_KEYS) delete process.env[key];
    process.env.ASSISTANT_MOCK = "0";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    for (const key of ENV_KEYS) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  });

  it("forwards the image block to Anthropic unchanged", async () => {
    const { chat } = await import("@/lib/llm/provider");
    process.env.LLM_PROVIDER = "anthropic";
    process.env.ANTHROPIC_API_KEY = "sk-test";
    const { calls } = captureFetch(() =>
      jsonResponse({ model: "claude-sonnet-4-5-20250929", content: [{ type: "text", text: "ok" }] }),
    );

    await chat({
      messages: [
        {
          role: "user",
          content: [{ type: "text", text: "What is this chart?" }, IMAGE_BLOCK],
        },
      ],
    });

    const body = bodyOf(calls[0]);
    expect(body.messages).toEqual([
      {
        role: "user",
        content: [
          { type: "text", text: "What is this chart?" },
          { type: "image", source: { type: "base64", media_type: "image/png", data: "AAA" } },
        ],
      },
    ]);
  });

  it("maps the image block to an image_url part on the xAI path", async () => {
    const { chat } = await import("@/lib/llm/provider");
    process.env.LLM_PROVIDER = "xai";
    process.env.XAI_API_KEY = "xai-test";
    const { calls } = captureFetch(() =>
      jsonResponse({ model: "grok-4", choices: [{ message: { content: "ok" } }] }),
    );

    await chat({
      messages: [
        {
          role: "user",
          content: [{ type: "text", text: "What is this chart?" }, IMAGE_BLOCK],
        },
      ],
    });

    const body = bodyOf(calls[0]);
    expect(body.messages).toEqual([
      {
        role: "user",
        content: [
          { type: "text", text: "What is this chart?" },
          { type: "image_url", image_url: { url: "data:image/png;base64,AAA" } },
        ],
      },
    ]);
  });

  it("keeps text-only turns on the plain-string content shape", async () => {
    const { toOpenAiMessages } = await import("@/lib/llm/provider");

    expect(
      toOpenAiMessages({
        system: "You are Radon.",
        messages: [
          { role: "user", content: "plain string turn" },
          { role: "assistant", content: [{ type: "text", text: "block turn" }] },
        ],
      }),
    ).toEqual([
      { role: "system", content: "You are Radon." },
      { role: "user", content: "plain string turn" },
      { role: "assistant", content: "block turn" },
    ]);
  });

  it("does not leak [object Object] into the mock prompt extraction", async () => {
    process.env.ASSISTANT_MOCK = "1";
    const { chat } = await import("@/lib/llm/provider");

    const response = await chat({
      messages: [{ role: "user", content: [{ type: "text", text: "read it" }, IMAGE_BLOCK] }],
    });

    expect(response.text).toContain("read it");
    expect(response.text).not.toContain("[object Object]");
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
