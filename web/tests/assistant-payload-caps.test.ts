/**
 * RC-B4: /api/assistant caps the whole turn, not just each message. Before
 * this, per-message image caps reset on every message and neither the message
 * count nor total serialized bytes were bounded, so one request could carry an
 * arbitrarily large payload into the model loop.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const ENV_KEYS = ["ASSISTANT_MOCK"];

function postRequest(body: unknown): Request {
  return new Request("http://localhost/api/assistant", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// 1x1 transparent PNG.
const PNG_B64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";

function imageBlock() {
  return { type: "image", source: { type: "base64", media_type: "image/png", data: PNG_B64 } };
}

describe("RC-B4: assistant turn payload caps", () => {
  const saved: Record<string, string | undefined> = {};

  beforeEach(() => {
    vi.resetModules();
    for (const key of ENV_KEYS) saved[key] = process.env[key];
    process.env.ASSISTANT_MOCK = "1";
    vi.doMock("@/lib/routeAccess", () => ({
      requireRouteAccess: vi.fn(async () => ({ ok: true, principal: { userId: "u", kind: "operator" } })),
    }));
    vi.doMock("@/lib/demo/enforceAiQuota", () => ({
      enforceDemoAiQuota: vi.fn(async () => null),
    }));
  });

  afterEach(() => {
    vi.doUnmock("@/lib/routeAccess");
    vi.doUnmock("@/lib/demo/enforceAiQuota");
    for (const key of ENV_KEYS) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
    vi.restoreAllMocks();
  });

  it("rejects a turn with more than the message cap", async () => {
    const { POST, MAX_MESSAGES_PER_TURN } = await import("@/app/api/assistant/route");
    const messages = Array.from({ length: MAX_MESSAGES_PER_TURN + 1 }, (_, i) => ({
      role: i % 2 ? "assistant" : "user",
      content: `m${i}`,
    }));
    const res = await POST(postRequest({ messages }) as never);
    expect(res.status).toBe(400);
  });

  it("caps image blocks across the WHOLE turn, not per message", async () => {
    const { POST, MAX_IMAGE_BLOCKS_PER_TURN } = await import("@/app/api/assistant/route");
    // Spread past the aggregate cap using messages each under the per-message cap.
    const messages = Array.from({ length: MAX_IMAGE_BLOCKS_PER_TURN + 1 }, () => ({
      role: "user",
      content: [{ type: "text", text: "look" }, imageBlock()],
    }));
    const res = await POST(postRequest({ messages }) as never);
    expect(res.status).toBe(400);
  });

  it("rejects a turn whose serialized payload exceeds the byte cap with 413", async () => {
    const { POST, MAX_TURN_PAYLOAD_BYTES } = await import("@/app/api/assistant/route");
    const messages = [{ role: "user", content: "x".repeat(MAX_TURN_PAYLOAD_BYTES + 1024) }];
    const res = await POST(postRequest({ messages }) as never);
    expect(res.status).toBe(413);
  });

  it("still accepts a normal turn", async () => {
    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(
      postRequest({ messages: [{ role: "user", content: "hello" }] }) as never,
    );
    expect(res.status).toBe(200);
  });
});
