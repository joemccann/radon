import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const remote = vi.hoisted(() => vi.fn());
vi.mock("@/lib/radonApi", () => ({ radonFetch: remote }));

beforeEach(() => { vi.stubEnv("ASSISTANT_MOCK", "0"); remote.mockReset(); });
afterEach(() => vi.unstubAllEnvs());

describe("shared model gateway web transport", () => {
  it("sends one complete request through FastAPI without resolving provider credentials", async () => {
    const { chat } = await import("@/lib/llm/provider");
    const signal = new AbortController().signal;
    const request = {
      system: "System", messages: [{ role: "user" as const, content: [
        { type: "text" as const, text: "Read this" },
        { type: "image" as const, source: { type: "base64" as const, media_type: "image/png", data: "AAAA" } },
        { type: "tool_result" as const, tool_use_id: "tool-1", content: "result" },
      ] }],
      tools: [{ name: "read_portfolio", input_schema: { type: "object" } }],
      toolChoice: "auto" as const, reasoningEffort: "low" as const,
      maxTokens: 500, timeoutMs: 90000, fallback: false, signal,
    };
    const response = { provider: "anthropic", model: "capable", text: "", toolCalls: [{ id: "tool-2", name: "read_portfolio", input: {} }], usedFallback: true };
    remote.mockResolvedValue(response);
    await expect(chat(request)).resolves.toEqual(response);
    expect(remote).toHaveBeenCalledTimes(1);
    const [url, options] = remote.mock.calls[0];
    expect(url).toBe("/model-gateway/chat");
    expect(options).toMatchObject({ method: "POST", timeout: 90000, signal });
    expect(JSON.parse(options.body)).toEqual({ ...request, signal: undefined });
    expect(JSON.parse(options.body)).not.toHaveProperty("provider");
  });

  it("never retries a rejected request with a local provider", async () => {
    const { chat } = await import("@/lib/llm/provider");
    remote.mockRejectedValue(new Error("Gateway exhausted"));
    await expect(chat({ messages: [{ role: "user", content: "hi" }] })).rejects.toThrow("Gateway exhausted");
    expect(remote).toHaveBeenCalledTimes(1);
  });

  it("rejects malformed gateway results", async () => {
    const { chat } = await import("@/lib/llm/provider");
    remote.mockResolvedValue({ provider: "anthropic", text: 7 });
    await expect(chat({ messages: [{ role: "user", content: "hi" }] })).rejects.toThrow(/malformed/i);
  });

  it("keeps offline mock mode local", async () => {
    vi.stubEnv("ASSISTANT_MOCK", "1");
    const { chat } = await import("@/lib/llm/provider");
    const response = await chat({ messages: [{ role: "user", content: [{ type: "text", text: "Read" }] }] });
    expect(response.text).toContain("Read");
    expect(remote).not.toHaveBeenCalled();
  });

  it("uses the gateway catalog and validates only service-listed model IDs", async () => {
    const { resolveModelCatalog, validateModelId } = await import("@/lib/llm/catalog");
    const catalog = { models: [{ id: "antigravity-default", provider: "antigravity", label: "ANTIGRAVITY", refreshedAt: "2026-10-30" }], defaultId: "antigravity-default", source: "gateway" };
    remote.mockResolvedValue(catalog);
    await expect(resolveModelCatalog()).resolves.toEqual(catalog);
    await expect(validateModelId("antigravity-default")).resolves.toEqual(catalog.models[0]);
    await expect(validateModelId("invented-model")).resolves.toBeNull();
    expect(remote).toHaveBeenCalledWith("/model-gateway/models", expect.objectContaining({ cache: "no-store" }));
  });
});
