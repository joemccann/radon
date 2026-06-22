import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { chat, type LlmChatRequest } from "@/lib/llm/provider";

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

const SAMPLE_REQUEST: LlmChatRequest = {
  system: "You are Radon.",
  messages: [{ role: "user", content: "What is flow?" }],
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
  "GROQ_API_KEY",
  "GROQ_BASE_URL",
  "GROQ_MODEL",
  "GEMINI_API_KEY",
  "GEMINI_BASE_URL",
  "GEMINI_MODEL",
];

describe("llm provider", () => {
  const saved: Record<string, string | undefined> = {};

  beforeEach(() => {
    for (const key of ENV_KEYS) saved[key] = process.env[key];
    for (const key of ENV_KEYS) delete process.env[key];
    // The package test runner forces ASSISTANT_MOCK=1 + NODE_ENV=test; opt OUT
    // of mock mode for the fetch-path tests. The mock test re-enables it.
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

  it("returns a mock completion without touching fetch when ASSISTANT_MOCK=1", async () => {
    process.env.ASSISTANT_MOCK = "1";
    const { calls } = captureFetch(() => jsonResponse({}));

    const result = await chat(SAMPLE_REQUEST);

    expect(calls).toHaveLength(0);
    expect(result.provider).toBe("anthropic");
    expect(result.text.length).toBeGreaterThan(0);
  });

  it("defaults to Anthropic with the native message shape", async () => {
    process.env.ANTHROPIC_API_KEY = "sk-ant-test";
    const { calls } = captureFetch(() =>
      jsonResponse({
        model: "claude-sonnet-4-5-20250929",
        content: [{ type: "text", text: "Flow is institutional positioning." }],
        stop_reason: "end_turn",
        usage: { input_tokens: 10, output_tokens: 5 },
      }),
    );

    const result = await chat(SAMPLE_REQUEST);

    expect(calls).toHaveLength(1);
    const call = calls[0];
    expect(call.url).toContain("api.anthropic.com");
    expect((call.init.headers as Record<string, string>)["x-api-key"]).toBe("sk-ant-test");
    expect((call.init.headers as Record<string, string>)["anthropic-version"]).toBe("2023-06-01");

    const payload = bodyOf(call);
    expect(payload.system).toBe("You are Radon.");
    expect(payload.messages).toEqual([{ role: "user", content: "What is flow?" }]);
    expect(payload).toHaveProperty("max_tokens");

    expect(result.provider).toBe("anthropic");
    expect(result.text).toBe("Flow is institutional positioning.");
    expect(result.usage).toEqual({ inputTokens: 10, outputTokens: 5 });
  });

  it("targets an OpenAI-compatible endpoint with chat/completions shape", async () => {
    process.env.LLM_PROVIDER = "openai";
    process.env.OPENAI_API_KEY = "sk-openai-test";
    const { calls } = captureFetch(() =>
      jsonResponse({
        model: "gpt-4o",
        choices: [{ message: { role: "assistant", content: "Flow is order flow." }, finish_reason: "stop" }],
        usage: { prompt_tokens: 7, completion_tokens: 3 },
      }),
    );

    const result = await chat(SAMPLE_REQUEST);

    expect(calls).toHaveLength(1);
    const call = calls[0];
    expect(call.url).toContain("/chat/completions");
    expect((call.init.headers as Record<string, string>)["authorization"] ?? (call.init.headers as Record<string, string>)["Authorization"]).toBe("Bearer sk-openai-test");

    const payload = bodyOf(call);
    const messages = payload.messages as Array<{ role: string; content: string }>;
    expect(messages[0]).toEqual({ role: "system", content: "You are Radon." });
    expect(messages[1]).toEqual({ role: "user", content: "What is flow?" });

    expect(result.provider).toBe("openai");
    expect(result.text).toBe("Flow is order flow.");
    expect(result.usage).toEqual({ inputTokens: 7, outputTokens: 3 });
  });

  it("honors a custom OpenAI-compatible base URL (Groq/DeepSeek/Ollama)", async () => {
    process.env.LLM_PROVIDER = "openai";
    process.env.OPENAI_API_KEY = "sk-local";
    process.env.OPENAI_BASE_URL = "http://localhost:11434/v1";
    const { calls } = captureFetch(() =>
      jsonResponse({ choices: [{ message: { content: "ok" } }] }),
    );

    await chat(SAMPLE_REQUEST);

    expect(calls[0].url).toBe("http://localhost:11434/v1/chat/completions");
  });

  it("selects Gemini and normalizes its response", async () => {
    process.env.LLM_PROVIDER = "gemini";
    process.env.GEMINI_API_KEY = "g-test";
    const { calls } = captureFetch(() =>
      jsonResponse({
        candidates: [{ content: { parts: [{ text: "Gemini flow answer." }] }, finishReason: "STOP" }],
        usageMetadata: { promptTokenCount: 4, candidatesTokenCount: 6 },
      }),
    );

    const result = await chat(SAMPLE_REQUEST);

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain("generativelanguage.googleapis.com");
    expect(result.provider).toBe("gemini");
    expect(result.text).toBe("Gemini flow answer.");
    expect(result.usage).toEqual({ inputTokens: 4, outputTokens: 6 });
  });

  it("falls back to the configured provider when the primary errors", async () => {
    process.env.LLM_PROVIDER = "openai";
    process.env.LLM_FALLBACK_PROVIDER = "anthropic";
    process.env.OPENAI_API_KEY = "sk-openai-test";
    process.env.ANTHROPIC_API_KEY = "sk-ant-test";

    const { calls } = captureFetch((call) => {
      if (call.url.includes("/chat/completions")) {
        return jsonResponse({ error: "rate limited" }, 429);
      }
      return jsonResponse({
        content: [{ type: "text", text: "Fallback answer." }],
        stop_reason: "end_turn",
      });
    });

    const result = await chat(SAMPLE_REQUEST);

    expect(calls).toHaveLength(2);
    expect(calls[0].url).toContain("/chat/completions");
    expect(calls[1].url).toContain("api.anthropic.com");
    expect(result.provider).toBe("anthropic");
    expect(result.text).toBe("Fallback answer.");
    expect(result.usedFallback).toBe(true);
  });

  it("throws when the primary errors and no fallback is configured", async () => {
    process.env.LLM_PROVIDER = "openai";
    process.env.OPENAI_API_KEY = "sk-openai-test";
    captureFetch(() => jsonResponse({ error: "boom" }, 500));

    await expect(chat(SAMPLE_REQUEST)).rejects.toThrow();
  });

  it("passes tools to providers that support tool_use and normalizes tool calls", async () => {
    process.env.ANTHROPIC_API_KEY = "sk-ant-test";
    const tools = [
      {
        name: "get_flow",
        description: "Get flow for a ticker",
        input_schema: { type: "object", properties: { ticker: { type: "string" } } },
      },
    ];
    const { calls } = captureFetch(() =>
      jsonResponse({
        content: [
          { type: "text", text: "Let me check." },
          { type: "tool_use", id: "tu_1", name: "get_flow", input: { ticker: "SPY" } },
        ],
        stop_reason: "tool_use",
      }),
    );

    const result = await chat({ ...SAMPLE_REQUEST, tools });

    const payload = bodyOf(calls[0]);
    expect(payload.tools).toEqual(tools);
    expect(result.toolCalls).toEqual([{ id: "tu_1", name: "get_flow", input: { ticker: "SPY" } }]);
  });
});
