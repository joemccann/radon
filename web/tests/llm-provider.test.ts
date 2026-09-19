import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  chat,
  resolveProvider,
  toOpenAiMessages,
  type LlmChatRequest,
} from "@/lib/llm/provider";
import { resetSubscriptionAuthCache } from "@/lib/llm/subscriptionAuth";

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
  "XAI_API_KEY",
  "GROK_API_KEY",
  "XAI_OAUTH_TOKEN",
  "GROK_OAUTH_TOKEN",
  "GROK_AUTH_FILE",
  "CLAUDE_CODE_OAUTH_TOKEN",
  "CLAUDE_CONFIG_DIR",
  "CODEX_OAUTH_TOKEN",
  "CODEX_ACCOUNT_ID",
  "CODEX_HOME",
  "RADON_LADDER_ALLOW_PREPAID",
  "CHATGPT_CODEX_RESPONSES_URL",
  "XAI_BASE_URL",
  "GROK_BASE_URL",
  "XAI_MODEL",
  "GROK_MODEL",
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
    // A developer laptop carries a real ~/.grok/auth.json; keep the operator's
    // subscription grant out of every case that does not opt in.
    process.env.GROK_AUTH_FILE = path.join(os.tmpdir(), "radon-no-grok-auth.json");
    process.env.CLAUDE_CONFIG_DIR = path.join(os.tmpdir(), "radon-no-claude-config");
    process.env.CODEX_HOME = path.join(os.tmpdir(), "radon-no-codex-home");
    resetSubscriptionAuthCache();
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

  it("auto-selects xAI when a Grok subscription grant is set and LLM_PROVIDER is unset", () => {
    process.env.XAI_OAUTH_TOKEN = "xai-test";
    expect(resolveProvider({})).toBe("xai");
  });

  it("aliases grok provider name to xai", () => {
    expect(resolveProvider({ provider: "grok" })).toBe("xai");
  });

  it("targets xAI chat/completions with Grok model defaults", async () => {
    process.env.XAI_OAUTH_TOKEN = "xai-test-key";
    const { calls } = captureFetch(() =>
      jsonResponse({
        model: "grok-4",
        choices: [{ message: { role: "assistant", content: "Grok flow take." }, finish_reason: "stop" }],
        usage: { prompt_tokens: 5, completion_tokens: 4 },
      }),
    );

    const result = await chat(SAMPLE_REQUEST);

    expect(calls).toHaveLength(1);
    const call = calls[0];
    expect(call.url).toBe("https://api.x.ai/v1/chat/completions");
    expect(
      (call.init.headers as Record<string, string>)["authorization"] ??
        (call.init.headers as Record<string, string>)["Authorization"],
    ).toBe("Bearer xai-test-key");

    const payload = bodyOf(call);
    // grok-4 is no longer a listed xAI model and reportedly bills as grok-4.3
    // rather than erroring, so the compiled-in default has to be the frontier
    // id the catalog also serves.
    expect(payload.model).toBe("grok-4.6");
    const messages = payload.messages as Array<{ role: string; content: string }>;
    expect(messages[0]).toEqual({ role: "system", content: "You are Radon." });
    expect(messages[1]).toEqual({ role: "user", content: "What is flow?" });

    expect(result.provider).toBe("xai");
    expect(result.text).toBe("Grok flow take.");
    expect(result.usage).toEqual({ inputTokens: 5, outputTokens: 4 });
  });

  it("serializes Anthropic-style tool rounds into OpenAI tool messages", () => {
    const messages = toOpenAiMessages({
      system: "sys",
      messages: [
        { role: "user", content: "check SPY" },
        {
          role: "assistant",
          content: [
            { type: "text", text: "Looking up." },
            { type: "tool_use", id: "call_1", name: "get_flow", input: { ticker: "SPY" } },
          ],
        },
        {
          role: "user",
          content: [{ type: "tool_result", tool_use_id: "call_1", content: "{\"ok\":true}" }],
        },
      ],
    });

    expect(messages[0]).toEqual({ role: "system", content: "sys" });
    expect(messages[1]).toEqual({ role: "user", content: "check SPY" });
    expect(messages[2]).toMatchObject({
      role: "assistant",
      content: "Looking up.",
      tool_calls: [
        {
          id: "call_1",
          type: "function",
          function: { name: "get_flow", arguments: "{\"ticker\":\"SPY\"}" },
        },
      ],
    });
    expect(messages[3]).toEqual({
      role: "tool",
      tool_call_id: "call_1",
      content: "{\"ok\":true}",
    });
  });

  it("defaults to Anthropic with the native message shape when no xAI key", async () => {
    process.env.CLAUDE_CODE_OAUTH_TOKEN = "sk-ant-oat-test";
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
    expect(call.init.signal).toBeInstanceOf(AbortSignal);
    expect((call.init.headers as Record<string, string>).authorization).toBe("Bearer sk-ant-oat-test");
    expect((call.init.headers as Record<string, string>)["anthropic-beta"]).toBe("oauth-2025-04-20");
    expect((call.init.headers as Record<string, string>)["anthropic-version"]).toBe("2023-06-01");

    const payload = bodyOf(call);
    // A Claude Max grant is honoured only for Claude Code traffic: the identity
    // block leads, the caller's system text follows.
    expect(payload.system).toEqual([
      { type: "text", text: "You are Claude Code, Anthropic's official CLI for Claude." },
      { type: "text", text: "You are Radon." },
    ]);
    expect(payload.messages).toEqual([{ role: "user", content: "What is flow?" }]);
    expect(payload).toHaveProperty("max_tokens");
    expect(payload.model).toBe("claude-opus-5");

    expect(result.provider).toBe("anthropic");
    expect(result.text).toBe("Flow is institutional positioning.");
    expect(result.usage).toEqual({ inputTokens: 10, outputTokens: 5 });
  });

  it("targets an OpenAI-compatible endpoint with chat/completions shape", async () => {
    process.env.LLM_PROVIDER = "openai";
    process.env.OPENAI_API_KEY = "sk-openai-test";
    process.env.RADON_LADDER_ALLOW_PREPAID = "1";
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
    expect(call.init.signal).toBeInstanceOf(AbortSignal);
    expect((call.init.headers as Record<string, string>)["authorization"] ?? (call.init.headers as Record<string, string>)["Authorization"]).toBe("Bearer sk-openai-test");

    const payload = bodyOf(call);
    expect(payload.model).toBe("gpt-5.5");
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
    process.env.RADON_LADDER_ALLOW_PREPAID = "1";
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
    process.env.RADON_LADDER_ALLOW_PREPAID = "1";
    const { calls } = captureFetch(() =>
      jsonResponse({
        candidates: [{ content: { parts: [{ text: "Gemini flow answer." }] }, finishReason: "STOP" }],
        usageMetadata: { promptTokenCount: 4, candidatesTokenCount: 6 },
      }),
    );

    const result = await chat(SAMPLE_REQUEST);

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain("generativelanguage.googleapis.com");
    expect(calls[0].init.signal).toBeInstanceOf(AbortSignal);
    expect(result.provider).toBe("gemini");
    expect(result.text).toBe("Gemini flow answer.");
    expect(result.usage).toEqual({ inputTokens: 4, outputTokens: 6 });
  });

  it("gemini tool request explicitly falls back", async () => {
    process.env.LLM_PROVIDER = "gemini";
    process.env.LLM_FALLBACK_PROVIDER = "openai";
    process.env.GEMINI_API_KEY = "g-test";
    process.env.RADON_LADDER_ALLOW_PREPAID = "1";
    process.env.OPENAI_API_KEY = "sk-test";
    process.env.RADON_LADDER_ALLOW_PREPAID = "1";
    const { calls } = captureFetch(() =>
      jsonResponse({ choices: [{ message: { content: "fallback" } }] }),
    );
    const result = await chat({ ...SAMPLE_REQUEST, tools: [{ name: "get_flow", input_schema: {} }] });
    expect(result.provider).toBe("openai");
    expect(result.usedFallback).toBe(true);
    expect(calls).toHaveLength(1);
  });

  it("falls back to the configured provider when the primary errors", async () => {
    process.env.LLM_PROVIDER = "openai";
    process.env.LLM_FALLBACK_PROVIDER = "anthropic";
    process.env.OPENAI_API_KEY = "sk-openai-test";
    process.env.RADON_LADDER_ALLOW_PREPAID = "1";
    process.env.CLAUDE_CODE_OAUTH_TOKEN = "sk-ant-oat-test";

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

  it("does not carry the picked model id into the fallback provider", async () => {
    // A picked model is scoped to the provider that owns it. Handing "grok-4.6"
    // to Anthropic turns a transient xAI blip into a hard 404 and kills the one
    // rescue path the operator configured.
    process.env.XAI_OAUTH_TOKEN = "xai-test-key";
    process.env.CLAUDE_CODE_OAUTH_TOKEN = "sk-ant-oat-test";
    process.env.LLM_FALLBACK_PROVIDER = "anthropic";

    const { calls } = captureFetch((call) => {
      if (call.url.includes("/chat/completions")) return jsonResponse({ error: "rate limited" }, 429);
      return jsonResponse({
        content: [{ type: "text", text: "Rescued." }],
        stop_reason: "end_turn",
      });
    });

    const result = await chat({ ...SAMPLE_REQUEST, model: "grok-4.6" });

    expect(calls).toHaveLength(2);
    expect(calls[0].url).toContain("api.x.ai");
    expect(bodyOf(calls[0]).model).toBe("grok-4.6");
    expect(calls[1].url).toContain("api.anthropic.com");
    expect(bodyOf(calls[1]).model).toBe("claude-opus-5");
    expect(result.provider).toBe("anthropic");
    expect(result.usedFallback).toBe(true);
  });

  it("does not let an explicit request.provider pin the fallback back onto the failed provider", async () => {
    process.env.OPENAI_API_KEY = "sk-openai-test";
    process.env.RADON_LADDER_ALLOW_PREPAID = "1";
    process.env.CLAUDE_CODE_OAUTH_TOKEN = "sk-ant-oat-test";
    process.env.LLM_FALLBACK_PROVIDER = "anthropic";

    const { calls } = captureFetch((call) => {
      if (call.url.includes("/chat/completions")) return jsonResponse({ error: "boom" }, 500);
      return jsonResponse({ content: [{ type: "text", text: "Rescued." }], stop_reason: "end_turn" });
    });

    const result = await chat({ ...SAMPLE_REQUEST, provider: "openai", model: "gpt-5.5" });

    expect(calls).toHaveLength(2);
    expect(calls[1].url).toContain("api.anthropic.com");
    expect(bodyOf(calls[1]).model).toBe("claude-opus-5");
    expect(result.usedFallback).toBe(true);
  });

  it("falls back to Anthropic by default when auto-preferred xAI fails and an Anthropic key exists", async () => {
    // 2026-09-18: the xAI team ran out of credits (403 permission-denied) and
    // every newsfeed voice rewrite surfaced "Voice rewrite unavailable" even
    // though ANTHROPIC_API_KEY was live on the host. Nothing had set
    // LLM_FALLBACK_PROVIDER, so the historical default provider never got a
    // turn. xAI is only auto-preferred; losing it must degrade, not fail.
    process.env.XAI_OAUTH_TOKEN = "xai-test-key";
    process.env.CLAUDE_CODE_OAUTH_TOKEN = "sk-ant-oat-test";
    delete process.env.LLM_FALLBACK_PROVIDER;

    const { calls } = captureFetch((call) => {
      if (call.url.includes("/chat/completions")) {
        return jsonResponse({ code: "permission-denied", error: "used all available credits" }, 403);
      }
      return jsonResponse({
        content: [{ type: "text", text: "Anthropic rescued." }],
        stop_reason: "end_turn",
      });
    });

    const result = await chat(SAMPLE_REQUEST);

    expect(calls).toHaveLength(2);
    expect(calls[0].url).toContain("/chat/completions");
    expect(calls[1].url).toContain("api.anthropic.com");
    expect(result.provider).toBe("anthropic");
    expect(result.usedFallback).toBe(true);
  });

  it("does not default a fallback when the primary was chosen explicitly", async () => {
    process.env.LLM_PROVIDER = "xai";
    process.env.XAI_OAUTH_TOKEN = "xai-test-key";
    process.env.CLAUDE_CODE_OAUTH_TOKEN = "sk-ant-oat-test";
    delete process.env.LLM_FALLBACK_PROVIDER;
    captureFetch(() => jsonResponse({ error: "boom" }, 500));

    await expect(chat(SAMPLE_REQUEST)).rejects.toThrow();
  });

  it("prefers the Grok subscription token in ~/.grok/auth.json over the prepaid xAI key", async () => {
    // Operator mandate 2026-09-18: prepaid xAI credits must never be the
    // meter for Grok; the SuperGrok subscription (the OIDC grant the grok CLI
    // writes and radon-subscription-tokens keeps live) is. api.x.ai accepts
    // that grant as a Bearer on /v1/chat/completions (verified live: 200).
    const authFile = path.join(os.tmpdir(), `grok-auth-${process.pid}-${Date.now()}.json`);
    fs.writeFileSync(authFile, JSON.stringify({
      "https://auth.x.ai::client": {
        key: "grok-subscription-token",
        auth_mode: "oidc",
        refresh_token: "r",
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        oidc_issuer: "https://auth.x.ai",
      },
    }));
    process.env.GROK_AUTH_FILE = authFile;
    process.env.XAI_API_KEY = "prepaid-should-not-be-used";
    process.env.RADON_LADDER_ALLOW_PREPAID = "1";
    const { calls } = captureFetch(() =>
      jsonResponse({ choices: [{ message: { role: "assistant", content: "ok" }, finish_reason: "stop" }] }),
    );
    try {
      const result = await chat(SAMPLE_REQUEST);
      expect(result.provider).toBe("xai");
      expect(calls[0].url).toContain("api.x.ai");
      expect(new Headers(calls[0].init.headers).get("authorization")).toBe("Bearer grok-subscription-token");
    } finally {
      fs.rmSync(authFile, { force: true });
    }
  });

  it("auto-prefers xAI on the subscription token alone, with no prepaid key set", async () => {
    process.env.XAI_OAUTH_TOKEN = "grok-subscription-token";
    process.env.CLAUDE_CODE_OAUTH_TOKEN = "sk-ant-oat-test";
    const { calls } = captureFetch(() =>
      jsonResponse({ choices: [{ message: { role: "assistant", content: "ok" }, finish_reason: "stop" }] }),
    );
    const result = await chat(SAMPLE_REQUEST);
    expect(result.provider).toBe("xai");
    expect(new Headers(calls[0].init.headers).get("authorization")).toBe("Bearer grok-subscription-token");
  });

  it("ignores an expired subscription entry and, only under RADON_LADDER_ALLOW_PREPAID, uses the prepaid key", async () => {
    const authFile = path.join(os.tmpdir(), `grok-auth-expired-${process.pid}-${Date.now()}.json`);
    fs.writeFileSync(authFile, JSON.stringify({
      "https://auth.x.ai::client": {
        key: "stale-subscription-token",
        auth_mode: "oidc",
        expires_at: new Date(Date.now() - 60_000).toISOString(),
      },
    }));
    process.env.GROK_AUTH_FILE = authFile;
    process.env.XAI_API_KEY = "prepaid-key";
    process.env.RADON_LADDER_ALLOW_PREPAID = "1";
    const { calls } = captureFetch(() =>
      jsonResponse({ choices: [{ message: { role: "assistant", content: "ok" }, finish_reason: "stop" }] }),
    );
    try {
      await chat(SAMPLE_REQUEST);
      expect(new Headers(calls[0].init.headers).get("authorization")).toBe("Bearer prepaid-key");
    } finally {
      fs.rmSync(authFile, { force: true });
    }
  });

  describe("subscriptions only (operator mandate 2026-09-18)", () => {
    it("never authenticates Anthropic with a prepaid key alone", async () => {
      process.env.ANTHROPIC_API_KEY = "sk-ant-prepaid";
      process.env.LLM_PROVIDER = "anthropic";
      const { calls } = captureFetch(() => jsonResponse({}));
      await expect(chat(SAMPLE_REQUEST)).rejects.toThrow(/Missing Anthropic subscription/);
      expect(calls).toHaveLength(0);
    });

    it("never selects or authenticates xAI on a prepaid key alone", async () => {
      process.env.XAI_API_KEY = "xai-prepaid";
      process.env.CLAUDE_CODE_OAUTH_TOKEN = "sk-ant-oat-test";
      expect(resolveProvider({})).toBe("anthropic");
      process.env.LLM_PROVIDER = "xai";
      const { calls } = captureFetch(() => jsonResponse({}));
      await expect(chat(SAMPLE_REQUEST)).rejects.toThrow(/Missing xAI subscription/);
      expect(calls).toHaveLength(0);
    });

    it("never authenticates OpenAI with a prepaid key alone", async () => {
      process.env.OPENAI_API_KEY = "sk-openai-prepaid";
      process.env.LLM_PROVIDER = "openai";
      const { calls } = captureFetch(() => jsonResponse({}));
      await expect(chat(SAMPLE_REQUEST)).rejects.toThrow(/Missing ChatGPT subscription/);
      expect(calls).toHaveLength(0);
    });

    it("never authenticates Gemini with a prepaid key alone", async () => {
      process.env.GEMINI_API_KEY = "g-prepaid";
      process.env.LLM_PROVIDER = "gemini";
      const { calls } = captureFetch(() => jsonResponse({}));
      await expect(chat(SAMPLE_REQUEST)).rejects.toThrow(/RADON_LADDER_ALLOW_PREPAID/);
      expect(calls).toHaveLength(0);
    });

    it("reads the Claude Max grant from the credentials file and skips an expired one", async () => {
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), "radon-claude-cfg-"));
      process.env.CLAUDE_CONFIG_DIR = dir;
      process.env.LLM_PROVIDER = "anthropic";
      const write = (expiresAt: number) =>
        fs.writeFileSync(
          path.join(dir, ".credentials.json"),
          JSON.stringify({ claudeAiOauth: { accessToken: "sk-ant-oat-file", refreshToken: "r", expiresAt, subscriptionType: "max" } }),
        );
      try {
        write(Date.now() - 60_000);
        resetSubscriptionAuthCache();
        await expect(chat(SAMPLE_REQUEST)).rejects.toThrow(/Missing Anthropic subscription/);
        write(Date.now() + 3_600_000);
        resetSubscriptionAuthCache();
        const { calls } = captureFetch(() =>
          jsonResponse({ content: [{ type: "text", text: "ok" }], stop_reason: "end_turn" }),
        );
        await chat(SAMPLE_REQUEST);
        expect((calls[0].init.headers as Record<string, string>).authorization).toBe("Bearer sk-ant-oat-file");
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });

    it("serves OpenAI through the ChatGPT codex Responses stream with the account id", async () => {
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), "radon-codex-home-"));
      process.env.CODEX_HOME = dir;
      process.env.LLM_PROVIDER = "openai";
      fs.writeFileSync(
        path.join(dir, "auth.json"),
        JSON.stringify({ auth_mode: "chatgpt", tokens: { access_token: "codex-grant", account_id: "acct-1", refresh_token: "r" } }),
      );
      resetSubscriptionAuthCache();
      const sse = [
        'data: {"type":"response.created","response":{"id":"r1"}}',
        'data: {"type":"response.output_text.delta","delta":"Hel"}',
        'data: {"type":"response.output_text.delta","delta":"lo"}',
        'data: {"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":3,"output_tokens":2}}}',
        "",
      ].join("\n");
      const { calls } = captureFetch(() => new Response(sse, { status: 200, headers: { "content-type": "text/event-stream" } }));
      try {
        const result = await chat(SAMPLE_REQUEST);
        expect(calls[0].url).toBe("https://chatgpt.com/backend-api/codex/responses");
        const headers = calls[0].init.headers as Record<string, string>;
        expect(headers.authorization).toBe("Bearer codex-grant");
        expect(headers["chatgpt-account-id"]).toBe("acct-1");
        const payload = bodyOf(calls[0]);
        expect(payload.stream).toBe(true);
        expect(payload.instructions).toBe("You are Radon.");
        expect(result).toMatchObject({ provider: "openai", text: "Hello", stopReason: "completed", usage: { inputTokens: 3, outputTokens: 2 } });
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });

    it("refuses a tool turn on the ChatGPT subscription so a tool-capable provider takes it", async () => {
      process.env.CODEX_OAUTH_TOKEN = "codex-grant";
      process.env.LLM_PROVIDER = "openai";
      process.env.LLM_FALLBACK_PROVIDER = "anthropic";
      process.env.CLAUDE_CODE_OAUTH_TOKEN = "sk-ant-oat-test";
      const { calls } = captureFetch(() =>
        jsonResponse({ content: [{ type: "text", text: "tools ok" }], stop_reason: "end_turn" }),
      );
      const result = await chat({ ...SAMPLE_REQUEST, tools: [{ name: "get_flow", input_schema: {} }] });
      expect(result.provider).toBe("anthropic");
      expect(result.usedFallback).toBe(true);
      expect(calls).toHaveLength(1);
      expect(calls[0].url).toContain("api.anthropic.com");
    });
  });

  it("throws when the primary errors and no fallback is configured", async () => {
    process.env.LLM_PROVIDER = "openai";
    process.env.OPENAI_API_KEY = "sk-openai-test";
    process.env.RADON_LADDER_ALLOW_PREPAID = "1";
    captureFetch(() => jsonResponse({ error: "boom" }, 500));

    await expect(chat(SAMPLE_REQUEST)).rejects.toThrow();
  });

  it("forwards tool_choice, reasoning_effort, and max_tokens on xAI chat completions", async () => {
    process.env.XAI_OAUTH_TOKEN = "xai-test-key";
    const { calls } = captureFetch(() =>
      jsonResponse({
        choices: [{ message: { role: "assistant", content: "September P&L." }, finish_reason: "stop" }],
      }),
    );

    await chat({
      ...SAMPLE_REQUEST,
      tools: [{ name: "get_realized_pnl", input_schema: { type: "object" } }],
      toolChoice: "none",
      reasoningEffort: "low",
      maxTokens: 8192,
    });

    const payload = bodyOf(calls[0]);
    expect(payload.tool_choice).toBe("none");
    expect(payload.reasoning_effort).toBe("low");
    expect(payload.max_tokens).toBe(8192);
    expect(payload.tools).toEqual([
      {
        type: "function",
        function: {
          name: "get_realized_pnl",
          parameters: { type: "object" },
        },
      },
    ]);
  });

  it("omits reasoning_effort and tool_choice on xAI when the caller did not set them", async () => {
    process.env.XAI_OAUTH_TOKEN = "xai-test-key";
    const { calls } = captureFetch(() =>
      jsonResponse({
        choices: [{ message: { role: "assistant", content: "ok" }, finish_reason: "stop" }],
      }),
    );

    await chat(SAMPLE_REQUEST);

    const payload = bodyOf(calls[0]);
    expect(payload).not.toHaveProperty("reasoning_effort");
    expect(payload).not.toHaveProperty("tool_choice");
    expect(payload).not.toHaveProperty("tools");
  });

  it("maps tool_choice none onto Anthropic's tool_choice object", async () => {
    process.env.CLAUDE_CODE_OAUTH_TOKEN = "sk-ant-oat-test";
    const { calls } = captureFetch(() =>
      jsonResponse({
        content: [{ type: "text", text: "Done." }],
        stop_reason: "end_turn",
      }),
    );

    await chat({
      ...SAMPLE_REQUEST,
      tools: [{ name: "get_flow", input_schema: { type: "object" } }],
      toolChoice: "none",
    });

    const payload = bodyOf(calls[0]);
    expect(payload.tool_choice).toEqual({ type: "none" });
    expect(payload.tools).toEqual([{ name: "get_flow", input_schema: { type: "object" } }]);
  });

  it("passes tools to providers that support tool_use and normalizes tool calls", async () => {
    process.env.CLAUDE_CODE_OAUTH_TOKEN = "sk-ant-oat-test";
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
