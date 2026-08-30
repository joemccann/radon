import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { drainAssistantStream } from "./assistantStream";

/**
 * Pasted-image attachments on the assistant turn.
 *
 * `safeMessages()` used to hard-drop any message whose content was not a
 * non-empty string, so an image turn reached the model as nothing at all.
 * Content may now also be an array of Anthropic-shaped blocks:
 *
 *   { type: "text", text }
 *   { type: "image", source: { type: "base64", media_type, data } }
 *
 * The route is the trust boundary: media_type allowlist, max 4 images per
 * message, 5 MB decoded per image, base64 charset. Anything failing a limit is
 * dropped SILENTLY (never thrown), and a message left with no usable block
 * never reaches the loop.
 */

const ENV_KEYS = ["ASSISTANT_MOCK", "NODE_ENV", "ANTHROPIC_API_KEY"];

const PNG_B64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

function postRequest(body: unknown): Request {
  return new Request("http://localhost/api/assistant", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function imageBlock(mediaType: string, data: string = PNG_B64) {
  return { type: "image", source: { type: "base64", media_type: mediaType, data } };
}

describe("assistant route image content blocks", () => {
  const saved: Record<string, string | undefined> = {};
  let runAssistantLoop: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.resetModules();
    for (const key of ENV_KEYS) saved[key] = process.env[key];
    process.env.ASSISTANT_MOCK = "1";

    runAssistantLoop = vi.fn(async () => ({
      content: "Read the chart.",
      model: "mock",
      toolEvents: [],
      rounds: 1,
      usage: { inputTokens: 1, outputTokens: 1 },
      outcome: "answered" as const,
    }));
    vi.doMock("@/lib/assistant/loop", async () => {
      const actual = await vi.importActual<typeof import("@/lib/assistant/loop")>("@/lib/assistant/loop");
      return { ...actual, runAssistantLoop };
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.doUnmock("@/lib/assistant/loop");
    for (const key of ENV_KEYS) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  });

  async function post(messages: unknown[]): Promise<Response> {
    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(postRequest({ messages }) as never);
    // The route answers as soon as the header is flushed and runs the loop on
    // the open stream, so nothing about the turn is settled until it closes.
    if (res.headers.get("content-type")?.includes("text/event-stream")) {
      await drainAssistantStream(res.clone());
    }
    return res;
  }

  function turns(): Array<{ role: string; content: unknown }> {
    expect(runAssistantLoop).toHaveBeenCalledTimes(1);
    return runAssistantLoop.mock.calls[0][0] as Array<{ role: string; content: unknown }>;
  }

  it("forwards a valid image block to the loop intact", async () => {
    const res = await post([
      {
        role: "user",
        content: [{ type: "text", text: "What is this chart saying?" }, imageBlock("image/png")],
      },
    ]);

    expect(res.status).toBe(200);
    expect(turns()).toEqual([
      {
        role: "user",
        content: [
          { type: "text", text: "What is this chart saying?" },
          { type: "image", source: { type: "base64", media_type: "image/png", data: PNG_B64 } },
        ],
      },
    ]);
  });

  it("accepts an image-only message with no text block", async () => {
    // The composer sends an image with no prompt as a complete turn, so the
    // route must forward it rather than treat a text-less message as empty.
    const res = await post([{ role: "user", content: [imageBlock("image/png")] }]);

    expect(res.status).toBe(200);
    expect(turns()).toEqual([
      {
        role: "user",
        content: [{ type: "image", source: { type: "base64", media_type: "image/png", data: PNG_B64 } }],
      },
    ]);
  });

  it("drops a non-allowlisted media type and keeps the text block", async () => {
    const res = await post([
      {
        role: "user",
        content: [{ type: "text", text: "Read this" }, imageBlock("image/svg+xml")],
      },
    ]);

    expect(res.status).toBe(200);
    expect(turns()[0].content).toEqual([{ type: "text", text: "Read this" }]);
  });

  it("drops the fifth image in one message and keeps the first four", async () => {
    const datas = ["AAAA", "BBBB", "CCCC", "DDDD", "EEEE"];
    const res = await post([
      {
        role: "user",
        content: [
          { type: "text", text: "Five charts" },
          ...datas.map((data) => imageBlock("image/png", data)),
        ],
      },
    ]);

    expect(res.status).toBe(200);
    const content = turns()[0].content as Array<{ type: string; source?: { data: string } }>;
    expect(content.filter((block) => block.type === "image").map((block) => block.source?.data)).toEqual([
      "AAAA",
      "BBBB",
      "CCCC",
      "DDDD",
    ]);
  });

  it("drops an image whose decoded payload exceeds 5 MB", async () => {
    // 8,000,000 base64 chars decode to ~6 MB.
    const oversized = "A".repeat(8_000_000);
    const res = await post([
      {
        role: "user",
        content: [{ type: "text", text: "Big one" }, imageBlock("image/jpeg", oversized)],
      },
    ]);

    expect(res.status).toBe(200);
    expect(turns()[0].content).toEqual([{ type: "text", text: "Big one" }]);
  });

  it("drops an image whose data is not base64", async () => {
    const res = await post([
      {
        role: "user",
        content: [{ type: "text", text: "Garbage" }, imageBlock("image/webp", "not base64!!! ***")],
      },
    ]);

    expect(res.status).toBe(200);
    expect(turns()[0].content).toEqual([{ type: "text", text: "Garbage" }]);
  });

  it("drops a message whose only block was rejected", async () => {
    const res = await post([
      { role: "user", content: [imageBlock("image/svg+xml")] },
      { role: "user", content: "Still here" },
    ]);

    expect(res.status).toBe(200);
    expect(turns()).toEqual([{ role: "user", content: "Still here" }]);
  });

  it("returns 400 when every message is dropped, without throwing", async () => {
    const res = await post([{ role: "user", content: [imageBlock("image/svg+xml")] }]);

    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "No messages supplied." });
    expect(runAssistantLoop).not.toHaveBeenCalled();
  });

  it("still forwards a plain string message unchanged", async () => {
    const res = await post([{ role: "user", content: "  How is SPY flow?  " }]);

    expect(res.status).toBe(200);
    expect(turns()).toEqual([{ role: "user", content: "How is SPY flow?" }]);
  });

  it("tells the model that attached images are part of the request", async () => {
    const { SYSTEM_PROMPT } = await import("@/app/api/assistant/route");
    expect(SYSTEM_PROMPT).toMatch(/image|screenshot|chart/i);
    expect(SYSTEM_PROMPT).toMatch(/attach/i);
  });
});
