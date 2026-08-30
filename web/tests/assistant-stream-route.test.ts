import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { parseSseFrames } from "./assistantStream";

/**
 * `/api/assistant` streams its envelope (R-262).
 *
 * 2026-08-29: a pasted-chart turn reached the operator as a 504 while
 * radon-nextjs was still working on it. The route ran the whole multi-round
 * `runAssistantLoop` and wrote its JSON only at the end, so NOTHING reached the
 * edge until the turn was finished and Caddy's header guard abandoned it. The
 * journal had already recorded a plain text turn answering at 55.3 s.
 *
 * Raising the guard only moves the cliff. The fix is that the header exists
 * before the loop is awaited:
 *
 *   start      flushed BEFORE `runAssistantLoop` — the whole fix
 *   heartbeat  every ~10 s, so no intermediary idles the connection out
 *   tool       one per tool call, as the loop completes it
 *   done       the AssistantResponse payload, unchanged in shape
 *   error      a mid-turn failure
 *
 * The subtlety: once the header is flushed there is no HTTP status left to
 * set. Every rejection that carries one — access, quota, the empty-turn guard —
 * must therefore run BEFORE the stream opens, and everything after it is an
 * `error` frame on a 200.
 */

const ENV_KEYS = ["ASSISTANT_MOCK", "NODE_ENV", "ANTHROPIC_API_KEY"];

function postRequest(body: unknown): Request {
  return new Request("http://localhost/api/assistant", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

const HELLO = [{ role: "user", content: "How is SPY flow?" }];

type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void; reject: (error: Error) => void };

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function loopResult(overrides: Record<string, unknown> = {}) {
  return {
    content: "Flow is neutral.",
    model: "mock",
    toolEvents: [],
    rounds: 1,
    usage: { inputTokens: 1, outputTokens: 1 },
    outcome: "answered" as const,
    ...overrides,
  };
}

describe("assistant route streams its envelope", () => {
  const saved: Record<string, string | undefined> = {};
  let runAssistantLoop: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.resetModules();
    for (const key of ENV_KEYS) saved[key] = process.env[key];
    process.env.ASSISTANT_MOCK = "1";
    runAssistantLoop = vi.fn(async () => loopResult());
    vi.doMock("@/lib/assistant/loop", async () => {
      const actual = await vi.importActual<typeof import("@/lib/assistant/loop")>(
        "@/lib/assistant/loop",
      );
      return { ...actual, runAssistantLoop };
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.doUnmock("@/lib/assistant/loop");
    vi.doUnmock("@/lib/routeAccess");
    vi.doUnmock("@/lib/demo/enforceAiQuota");
    for (const key of ENV_KEYS) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  });

  async function post(body: unknown = { messages: HELLO }): Promise<Response> {
    const { POST } = await import("@/app/api/assistant/route");
    return POST(postRequest(body) as never);
  }

  it("answers with an event stream", async () => {
    const res = await post();
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/event-stream");
    // A proxy that transforms or caches this body reintroduces the buffering
    // the stream exists to avoid.
    expect(res.headers.get("cache-control")).toContain("no-transform");
    await res.text();
  });

  it("flushes the start event BEFORE the loop resolves", async () => {
    // The whole fix. A loop that never settles stands in for the 55.3 s turn
    // the journal recorded: the header and first frame must exist anyway.
    const hanging = deferred<ReturnType<typeof loopResult>>();
    runAssistantLoop.mockImplementation(() => hanging.promise);

    const res = await post();
    expect(res.status).toBe(200);

    const reader = res.body!.getReader();
    const first = new TextDecoder().decode((await reader.read()).value);
    expect(first).toContain("event: start");

    hanging.resolve(loopResult());
    await reader.cancel();
  });

  it("keeps sending heartbeats while a slow loop runs", async () => {
    vi.useFakeTimers();
    const hanging = deferred<ReturnType<typeof loopResult>>();
    runAssistantLoop.mockImplementation(() => hanging.promise);

    const res = await post();
    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    // Drain the start frame first so the reads below can only be heartbeats.
    expect(decoder.decode((await reader.read()).value)).toContain("event: start");

    const { ASSISTANT_HEARTBEAT_MS } = await import("@/app/api/assistant/route");
    await vi.advanceTimersByTimeAsync(ASSISTANT_HEARTBEAT_MS + 1);
    expect(decoder.decode((await reader.read()).value)).toContain("event: heartbeat");
    await vi.advanceTimersByTimeAsync(ASSISTANT_HEARTBEAT_MS + 1);
    expect(decoder.decode((await reader.read()).value)).toContain("event: heartbeat");

    hanging.resolve(loopResult());
    await reader.cancel();
  });

  it("emits a tool event as the loop completes each call", async () => {
    runAssistantLoop.mockImplementation(
      async (
        _turns: unknown,
        _system: unknown,
        _principal: unknown,
        _selection: unknown,
        onToolEvent?: (event: unknown) => void,
      ) => {
        onToolEvent?.({ name: "get_portfolio", input: {}, ok: true });
        onToolEvent?.({ name: "get_quote", input: { ticker: "TLT" }, ok: true });
        return loopResult({
          toolEvents: [
            { name: "get_portfolio", input: {}, ok: true },
            { name: "get_quote", input: { ticker: "TLT" }, ok: true },
          ],
        });
      },
    );

    const frames = parseSseFrames(await (await post()).text());
    const tools = frames.filter((frame) => frame.event === "tool");
    expect(tools.map((frame) => (frame.data as { name: string }).name)).toEqual([
      "get_portfolio",
      "get_quote",
    ]);
    // The trailing payload still carries the authoritative list.
    expect((frames.at(-1)!.data as { toolEvents: unknown[] }).toolEvents).toHaveLength(2);
  });

  it("closes with a done frame carrying the unchanged payload shape", async () => {
    runAssistantLoop.mockResolvedValue(
      loopResult({ content: "Flow is neutral.", model: "grok-4.6", rounds: 2 }),
    );
    const frames = parseSseFrames(await (await post()).text());
    expect(frames[0].event).toBe("start");
    const done = frames.at(-1)!;
    expect(done.event).toBe("done");
    expect(done.data).toEqual({
      content: "Flow is neutral.",
      model: "grok-4.6",
      toolEvents: [],
      proposal: null,
      rounds: 2,
    });
  });

  it("reports a mid-turn failure as an error frame, not a status", async () => {
    // The header is already on the wire by the time the loop throws, so there
    // is no status left to set. A 502 here would be a lie the client cannot
    // read.
    runAssistantLoop.mockRejectedValue(new Error("provider exploded"));
    const res = await post();
    expect(res.status).toBe(200);
    const frames = parseSseFrames(await res.text());
    expect(frames.map((frame) => frame.event)).toContain("error");
    expect((frames.at(-1)!.data as { error: string }).error).toBe("provider exploded");
    expect(frames.some((frame) => frame.event === "done")).toBe(false);
  });

  describe("rejections that carry a status run before the stream opens", () => {
    it("still 400s an empty turn", async () => {
      const res = await post({ messages: [] });
      expect(res.status).toBe(400);
      expect(await res.json()).toEqual({ error: "No messages supplied." });
      expect(runAssistantLoop).not.toHaveBeenCalled();
    });

    it("still 401s when route access is refused", async () => {
      vi.doMock("@/lib/routeAccess", () => ({
        requireRouteAccess: vi.fn(async () => ({
          ok: false,
          response: new Response(JSON.stringify({ error: "Unauthorized" }), { status: 401 }),
        })),
      }));
      const res = await post();
      expect(res.status).toBe(401);
      expect(runAssistantLoop).not.toHaveBeenCalled();
    });

    it("still 429s when the demo AI quota is exhausted", async () => {
      vi.doMock("@/lib/demo/enforceAiQuota", () => ({
        enforceDemoAiQuota: vi.fn(async () =>
          new Response(JSON.stringify({ error: "Demo AI limit reached" }), { status: 429 }),
        ),
      }));
      const res = await post();
      expect(res.status).toBe(429);
      expect(runAssistantLoop).not.toHaveBeenCalled();
    });
  });
});
