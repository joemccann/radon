import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { assistantDonePayload } from "./assistantStream";

/**
 * REL-161 (R-454, R-457): the turn record says what left the box and what
 * ran it.
 *
 *   - `assistant_turns` carries `image_count` and the resolved provider /
 *     model, so a leak review can say which turns shipped a screenshot to
 *     which vendor.
 *   - a picked model id the catalog does not know is a 400, not a silent
 *     swap to another vendor's default.
 *   - a provider fallback is visible: the `done` frame carries
 *     `usedFallback: true` next to the id the operator actually requested.
 */

const ENV_KEYS = ["ASSISTANT_MOCK", "NODE_ENV", "ANTHROPIC_API_KEY"];

const PNG_B64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

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

describe("assistant turn provenance", () => {
  const saved: Record<string, string | undefined> = {};
  let dbExecute: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.resetModules();
    for (const key of ENV_KEYS) saved[key] = process.env[key];
    process.env.ASSISTANT_MOCK = "1";
    dbExecute = vi.fn().mockResolvedValue({ rows: [] });
    vi.doMock("@/lib/dbExecute", () => ({ dbExecute }));
    vi.spyOn(console, "log").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.doUnmock("@/lib/dbExecute");
    vi.doUnmock("@/lib/llm/provider");
    vi.doUnmock("@/lib/llm/catalog");
    for (const key of ENV_KEYS) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  });

  it("records image_count and the resolved provider/model on assistant_turns", async () => {
    const chat = vi.fn(async () => ({
      provider: "xai",
      model: "grok-4.6",
      text: "Read the chart.",
      stopReason: "end_turn",
      usage: { inputTokens: 5, outputTokens: 2 },
    }));
    vi.doMock("@/lib/llm/provider", () => ({ chat }));

    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(
      postRequest({
        messages: [
          {
            role: "user",
            content: [
              { type: "text", text: "What does this chart say?" },
              { type: "image", source: { type: "base64", media_type: "image/png", data: PNG_B64 } },
            ],
          },
        ],
      }) as never,
    );
    expect(res.status).toBe(200);
    await assistantDonePayload(res);

    await vi.waitFor(() => expect(dbExecute).toHaveBeenCalledTimes(1));
    const [stmt] = dbExecute.mock.calls[0];
    expect(stmt.sql).toMatch(
      /INSERT INTO assistant_turns \(ts, user_msg, rounds, tool_calls, usage, outcome, image_count, provider, model, error_class\)/,
    );
    expect(stmt.args.slice(5)).toEqual(["answered", 1, "xai", "grok-4.6", null]);
  });

  it("recordAssistantTurn writes image_count 0 and NULL provenance when absent", async () => {
    const { recordAssistantTurn } = await import("@/lib/assistant/telemetry");
    recordAssistantTurn({
      ts: "2026-08-30T12:00:00.000Z",
      userMsg: "hi",
      rounds: 0,
      toolCalls: [],
      outcome: "error",
    });
    await vi.waitFor(() => expect(dbExecute).toHaveBeenCalledTimes(1));
    const [stmt] = dbExecute.mock.calls[0];
    expect(stmt.args.slice(5)).toEqual(["error", 0, null, null, null]);
  });

  it("returns 400 for a model id the catalog does not know, before the stream opens", async () => {
    const validateModelId = mockCatalog();
    const chat = vi.fn();
    vi.doMock("@/lib/llm/provider", () => ({ chat }));

    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(
      postRequest({ messages: [{ role: "user", content: "How is SPY flow?" }], model: "claude-opus-9000-free" }) as never,
    );

    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "Unknown model id: claude-opus-9000-free" });
    expect(validateModelId).toHaveBeenCalledWith("claude-opus-9000-free");
    expect(chat).not.toHaveBeenCalled();
  });

  it("a forced provider fallback surfaces usedFallback and the requested id in the done frame", async () => {
    mockCatalog();
    const chat = vi.fn(async () => ({
      provider: "anthropic",
      model: "claude-opus-5",
      text: "Flow is neutral.",
      stopReason: "end_turn",
      usage: { inputTokens: 5, outputTokens: 2 },
      usedFallback: true,
    }));
    vi.doMock("@/lib/llm/provider", () => ({ chat }));

    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(
      postRequest({ messages: [{ role: "user", content: "How is SPY flow?" }], model: "grok-4.6" }) as never,
    );
    expect(res.status).toBe(200);
    const done = await assistantDonePayload<Record<string, unknown>>(res);
    expect(done).toMatchObject({ model: "claude-opus-5", requestedModel: "grok-4.6", usedFallback: true });

    await vi.waitFor(() => expect(dbExecute).toHaveBeenCalledTimes(1));
    const [stmt] = dbExecute.mock.calls[0];
    expect(stmt.args.slice(6)).toEqual([0, "anthropic", "claude-opus-5", null]);
  });

  it("a turn that ran on the requested model carries requestedModel and no usedFallback", async () => {
    mockCatalog();
    const chat = vi.fn(async () => ({
      provider: "xai",
      model: "grok-4.6",
      text: "Flow is neutral.",
      stopReason: "end_turn",
    }));
    vi.doMock("@/lib/llm/provider", () => ({ chat }));

    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(
      postRequest({ messages: [{ role: "user", content: "How is SPY flow?" }], model: "grok-4.6" }) as never,
    );
    const done = await assistantDonePayload<Record<string, unknown>>(res);
    expect(done.model).toBe("grok-4.6");
    expect(done.requestedModel).toBe("grok-4.6");
    expect(done).not.toHaveProperty("usedFallback");
  });
});
