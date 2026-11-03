import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { chat } from "@/lib/llm/provider";
const remote = vi.hoisted(() => vi.fn());
vi.mock("@/lib/radonApi", () => ({ radonFetch: remote }));
beforeEach(() => { vi.stubEnv("ASSISTANT_MOCK", "0"); remote.mockReset(); });
afterEach(() => vi.unstubAllEnvs());
// Native Codex SSE terminal validation is owned by the model service. A
// truncated/failed envelope must still never escape as a successful web reply.
it.each(["failed", "incomplete", "error"])("rejects gateway terminal %s", async stopReason => {
  remote.mockResolvedValue({ provider: "openai", model: "default", text: "partial", stopReason });
  await expect(chat({ messages: [] })).rejects.toThrow(/incomplete/);
});
it("preserves service-verified completion and usage", async () => {
  remote.mockResolvedValue({ provider: "openai", model: "default", text: "complete", stopReason: "completed", usage: { inputTokens: 3, outputTokens: 2 } });
  await expect(chat({ messages: [] })).resolves.toMatchObject({ text: "complete", stopReason: "completed", usage: { inputTokens: 3, outputTokens: 2 } });
});
it("never turns a service rejection into partial text or a second provider request", async () => {
  remote.mockRejectedValue(new Error("Upstream stream failed"));
  await expect(chat({ messages: [] })).rejects.toThrow("Upstream stream failed");
  expect(remote).toHaveBeenCalledTimes(1);
});
