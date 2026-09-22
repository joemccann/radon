import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { chat } from "@/lib/llm/provider";
import { resetSubscriptionAuthCache } from "@/lib/llm/subscriptionAuth";

const request = { messages: [{ role: "user" as const, content: "review" }] };
const delta = `data: ${JSON.stringify({ type: "response.output_text.delta", delta: '{"ok":true}' })}\n\n`;
const completed = 'data: {"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":3,"output_tokens":2}}}\n\n';
const badTerminals = [
  "",
  'data: {"type":"response.failed","error":{"message":"FAKE-sensitive"}}\n\n',
  'data: {"type":"error","message":"FAKE-sensitive"}\n\n',
  'data: {"type":"response.incomplete"}\n\n',
  'data: {"type":"response.completed"}\n\n',
  'data: {"type":"response.completed","response":[]}\n\n',
  'data: {"type":"response.completed","response":{"status":"failed"}}\n\n',
  'data: {"type":"response.completed",\n\n',
  completed + 'data: {"type":"response.failed"}\n\n',
];

beforeEach(() => {
  vi.stubEnv("ASSISTANT_MOCK", "0");
  vi.stubEnv("LLM_PROVIDER", "openai");
  vi.stubEnv("LLM_FALLBACK_PROVIDER", "");
  vi.stubEnv("RADON_LADDER_ALLOW_PREPAID", "0");
  vi.stubEnv("CODEX_OAUTH_TOKEN", "FAKE-codex-grant");
  vi.stubEnv("CLAUDE_CONFIG_DIR", "/nonexistent/radon-test-auth");
  vi.stubEnv("CLAUDE_CODE_OAUTH_TOKEN", "");
  resetSubscriptionAuthCache();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  resetSubscriptionAuthCache();
});

it.each(badTerminals)("refuses incomplete public chat: %s", async (terminal) => {
  const response = new Response(delta + terminal);
  vi.stubGlobal("fetch", vi.fn(async () => response));
  await expect(chat(request)).rejects.toThrow(/subscription.*stream/i);
  expect(response.bodyUsed).toBe(true);
});

it("retains completed text and usage", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(delta + completed)));
  await expect(chat(request)).resolves.toMatchObject({
    text: '{"ok":true}', stopReason: "completed", usage: { inputTokens: 3, outputTokens: 2 },
  });
});

it("uses configured fallback after failed terminal without leaking partial text", async () => {
  vi.stubEnv("LLM_FALLBACK_PROVIDER", "anthropic");
  vi.stubEnv("CLAUDE_CODE_OAUTH_TOKEN", "FAKE-claude-grant");
  const response = new Response(delta + badTerminals[1]);
  const fetch = vi.fn(async (url: string) => String(url).includes("chatgpt.com") ? response : new Response(JSON.stringify({
    content: [{ type: "text", text: "fallback complete" }], stop_reason: "end_turn",
  })));
  vi.stubGlobal("fetch", fetch);
  await expect(chat(request)).resolves.toMatchObject({ provider: "anthropic", text: "fallback complete", usedFallback: true });
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(response.bodyUsed).toBe(true);
});
