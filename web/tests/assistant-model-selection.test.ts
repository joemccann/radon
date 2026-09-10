import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { drainAssistantStream } from "./assistantStream";

/**
 * Model selection on the assistant request path.
 *
 * The picker sends a model id with the turn. The route honours it ONLY when the
 * catalog recognises it — a client must never be able to bill an arbitrary
 * model string — and an unknown id is a 400 (R-457): a silent swap onto another
 * vendor's default ran the turn at a different price and capability with no
 * signal. Only a catalog that is DOWN degrades to the deployment default.
 *
 * Provider routing is derived from the model id itself: picking a Grok model
 * must reach xAI even on a host whose default provider is Anthropic.
 */

const ENV_KEYS = [
  "ASSISTANT_MOCK",
  "NODE_ENV",
  "ANTHROPIC_API_KEY",
  "ANTHROPIC_MODEL",
  "XAI_API_KEY",
  "GROK_API_KEY",
  "XAI_MODEL",
  "OPENAI_API_KEY",
  "LLM_PROVIDER",
  "LLM_FALLBACK_PROVIDER",
];

const CATALOG_ROWS = [
  { id: "claude-opus-5", provider: "anthropic", label: "CLAUDE OPUS 5", refreshedAt: "2026-08-29" },
  { id: "grok-4.6", provider: "xai", label: "GROK", refreshedAt: "2026-08-29" },
];

function postRequest(body: unknown): Request {
  return new Request("http://localhost/api/assistant", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function mockCatalog() {
  const validateModelId = vi.fn(async (id: unknown) => CATALOG_ROWS.find((row) => row.id === id) ?? null);
  vi.doMock("@/lib/llm/catalog", () => ({ validateModelId }));
  return validateModelId;
}

function mockChat() {
  const chat = vi.fn(async () => ({
    provider: "anthropic",
    model: "mock",
    text: "Flow is neutral.",
    stopReason: "end_turn",
  }));
  vi.doMock("@/lib/llm/provider", () => ({ chat }));
  return chat;
}

describe("assistant model selection", () => {
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
    vi.doUnmock("@/lib/llm/catalog");
    for (const key of ENV_KEYS) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  });

  it("passes a catalogued model id to chat() as { provider, model }", async () => {
    const validateModelId = mockCatalog();
    const chat = mockChat();

    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(
      postRequest({ messages: [{ role: "user", content: "How is SPY flow?" }], model: "claude-opus-5" }) as never,
    );

    expect(res.status).toBe(200);
    await drainAssistantStream(res);
    expect(validateModelId).toHaveBeenCalledWith("claude-opus-5");
    expect(chat).toHaveBeenCalledTimes(1);
    expect(chat.mock.calls[0][0]).toMatchObject({ model: "claude-opus-5", provider: "anthropic" });
  });

  it("rejects an uncatalogued model string with 400 and never bills a default", async () => {
    mockCatalog();
    const chat = mockChat();

    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(
      postRequest({
        messages: [{ role: "user", content: "How is SPY flow?" }],
        model: "claude-opus-9000-free",
      }) as never,
    );

    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "Unknown model id: claude-opus-9000-free" });
    expect(chat).not.toHaveBeenCalled();
  });

  it("no model in the payload reproduces today's behavior — catalog untouched", async () => {
    const validateModelId = mockCatalog();
    const chat = mockChat();

    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(postRequest({ messages: [{ role: "user", content: "How is SPY flow?" }] }) as never);

    expect(res.status).toBe(200);
    await drainAssistantStream(res);
    expect(validateModelId).not.toHaveBeenCalled();
    expect(chat).toHaveBeenCalledTimes(1);
    const request = chat.mock.calls[0][0] as Record<string, unknown>;
    expect(request).not.toHaveProperty("model");
    expect(request).not.toHaveProperty("provider");
    expect(request).toMatchObject({ system: expect.any(String) });
  });

  it("a catalog lookup failure never fails the turn", async () => {
    const validateModelId = vi.fn(async () => {
      throw new Error("Turso unreachable");
    });
    vi.doMock("@/lib/llm/catalog", () => ({ validateModelId }));
    const chat = mockChat();

    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(
      postRequest({ messages: [{ role: "user", content: "How is SPY flow?" }], model: "grok-4.6" }) as never,
    );

    expect(res.status).toBe(200);
    await drainAssistantStream(res);
    const request = chat.mock.calls[0][0] as Record<string, unknown>;
    expect(request).not.toHaveProperty("model");
  });

  it("routes a grok model to xAI even when ANTHROPIC_API_KEY is the only default", async () => {
    process.env.ANTHROPIC_API_KEY = "sk-ant-test";
    delete process.env.XAI_API_KEY;
    delete process.env.GROK_API_KEY;

    const { resolveProvider } = await import("@/lib/llm/provider");
    expect(resolveProvider({ model: "grok-4.6" })).toBe("xai");
    expect(resolveProvider({})).toBe("anthropic");
  });

  it("routes a claude model to Anthropic even when XAI_API_KEY would win", async () => {
    process.env.ANTHROPIC_API_KEY = "sk-ant-test";
    process.env.XAI_API_KEY = "xai-test";

    const { resolveProvider } = await import("@/lib/llm/provider");
    expect(resolveProvider({ model: "claude-opus-5" })).toBe("anthropic");
    expect(resolveProvider({})).toBe("xai");
    // An explicit provider still outranks the model id.
    expect(resolveProvider({ model: "claude-opus-5", provider: "xai" })).toBe("xai");
  });

  it("a selected model beats LLM_PROVIDER on the host", async () => {
    process.env.LLM_PROVIDER = "anthropic";
    process.env.ANTHROPIC_API_KEY = "sk-ant-test";
    delete process.env.XAI_API_KEY;
    delete process.env.GROK_API_KEY;

    const { resolveProvider } = await import("@/lib/llm/provider");
    expect(resolveProvider({ model: "grok-4.6" })).toBe("xai");
    expect(resolveProvider({})).toBe("anthropic");
  });

  it("sends the requested model on the wire, overriding the env default", async () => {
    process.env.ASSISTANT_MOCK = "0";
    process.env.ANTHROPIC_API_KEY = "sk-ant-test";
    process.env.ANTHROPIC_MODEL = "claude-sonnet-5";
    // xAI would otherwise win resolveProvider(); the model id must override it.
    process.env.XAI_API_KEY = "xai-test";

    const fetchSpy = vi.fn(
      async () =>
        new Response(JSON.stringify({ model: "claude-opus-5", content: [{ type: "text", text: "ok" }] }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchSpy as unknown as typeof fetch);

    const { chat } = await import("@/lib/llm/provider");
    await chat({ messages: [{ role: "user", content: "hi" }], model: "claude-opus-5" });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.anthropic.com/v1/messages");
    expect(JSON.parse(init.body as string).model).toBe("claude-opus-5");
  });

  it("sends a grok model to the xAI base url, not Anthropic", async () => {
    process.env.ASSISTANT_MOCK = "0";
    process.env.ANTHROPIC_API_KEY = "sk-ant-test";
    process.env.XAI_API_KEY = "xai-test";
    process.env.LLM_PROVIDER = "anthropic";

    const fetchSpy = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ model: "grok-4.6", choices: [{ message: { content: "ok" }, finish_reason: "stop" }] }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
    );
    vi.stubGlobal("fetch", fetchSpy as unknown as typeof fetch);

    const { chat } = await import("@/lib/llm/provider");
    const result = await chat({ messages: [{ role: "user", content: "hi" }], model: "grok-4.6" });

    expect(result.provider).toBe("xai");
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.x.ai/v1/chat/completions");
    expect(JSON.parse(init.body as string).model).toBe("grok-4.6");
  });
});
