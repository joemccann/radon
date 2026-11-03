import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { chat, assistantChat, normalizeProvider, providerForModel } from "@/lib/llm/provider";

beforeEach(() => { vi.stubEnv("ASSISTANT_MOCK", "0"); vi.stubEnv("RADON_SERVICE_TOKEN", "fake-app-service-token"); });
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

function gatewayResponse(value: unknown, status = 200) {
  const fetch = vi.fn(async () => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("provider-neutral model gateway client", () => {
  it("authenticates with the application token, never a provider grant", async () => {
    vi.stubEnv("CLAUDE_CODE_OAUTH_TOKEN", "private-operator-grant");
    const fetch = gatewayResponse({ provider: "antigravity", model: "account-default", text: "ok", usage: { inputTokens: 3, outputTokens: 2 } });
    await expect(chat({ messages: [{ role: "user", content: "hi" }] })).resolves.toMatchObject({ provider: "antigravity", text: "ok" });
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/model-gateway\/chat$/);
    expect(new Headers(init.headers).get("X-Radon-Service-Token")).toBe("fake-app-service-token");
    expect(JSON.stringify(init)).not.toContain("private-operator-grant");
    expect(new Headers(init.headers).get("Authorization")).toBeNull();
    expect(init.cache).toBe("no-store");
  });

  it("forwards explicit selection as preferences and preserves service fallback provenance", async () => {
    const fetch = gatewayResponse({ provider: "anthropic", model: "fallback", text: "complete", usedFallback: true });
    await expect(chat({ provider: "xai", model: "grok-selected", fallback: false, messages: [] })).resolves.toMatchObject({ usedFallback: true });
    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toMatchObject({ provider: "xai", model: "grok-selected", fallback: false });
  });

  it("does not replay a failed model POST", async () => {
    const fetch = gatewayResponse({ detail: { code: "MODEL_EXHAUSTED", message: "All available providers exhausted" } }, 503);
    await expect(chat({ messages: [] })).rejects.toThrow(/MODEL_EXHAUSTED/);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("propagates cancellation without contacting a vendor", async () => {
    const controller = new AbortController();
    controller.abort();
    const fetch = gatewayResponse({ provider: "antigravity", model: "default", text: "late" });
    await expect(chat({ messages: [], signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("keeps assistantChat on the same service interface", async () => {
    const fetch = gatewayResponse({ provider: "antigravity", model: "default", text: "answer" });
    await assistantChat([{ role: "user", content: "hi" }], "System", { maxTokens: 40 });
    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toMatchObject({ system: "System", maxTokens: 40 });
  });

  it("keeps aliases and model ownership pure", () => {
    expect(normalizeProvider("grok")).toBe("xai");
    expect(providerForModel("grok-4.6")).toBe("xai");
    expect(providerForModel("claude-opus-5")).toBe("anthropic");
    expect(providerForModel("gpt-5.5")).toBe("openai");
    expect(providerForModel("antigravity-default")).toBe("antigravity");
  });
});
