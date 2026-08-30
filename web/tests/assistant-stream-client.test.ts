import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { requestAssistantTurn } from "@/lib/chat";

/**
 * Client half of the streamed assistant turn (R-262).
 *
 * `requestAssistantTurn` now reads `text/event-stream` off the response body.
 * The failure this has to make impossible: a stream that ends WITHOUT a `done`
 * frame — a killed radon-nextjs, a severed connection, a proxy that gives up
 * mid-body — must surface as an error the operator can see. Rendering it as an
 * empty assistant bubble is a worse bug than the 504 it replaces, because
 * nothing on screen says the turn failed.
 */

function sseResponse(frames: string[], init: ResponseInit = {}): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
    ...init,
  });
}

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

const DONE = {
  content: "Flow is neutral.",
  model: "grok-4.6",
  toolEvents: [{ name: "get_flow", input: {}, ok: true }],
  proposal: null,
  rounds: 2,
};

describe("requestAssistantTurn consumes the event stream", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("asks for the stream and returns the done payload", async () => {
    fetchMock.mockResolvedValue(
      sseResponse([frame("start", {}), frame("heartbeat", { ms: 10 }), frame("done", DONE)]),
    );

    const turn = await requestAssistantTurn([], "How is SPY flow?");

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get("accept")).toContain("text/event-stream");
    expect(turn.content).toContain("Flow is neutral.");
    expect(turn.model).toBe("grok-4.6");
    expect(turn.toolEvents).toHaveLength(1);
    expect(turn.proposal).toBeNull();
  });

  it("reports start and each tool event as it arrives", async () => {
    fetchMock.mockResolvedValue(
      sseResponse([
        frame("start", {}),
        frame("tool", { name: "get_portfolio", input: {}, ok: true }),
        frame("tool", { name: "get_quote", input: { ticker: "TLT" }, ok: true }),
        frame("done", DONE),
      ]),
    );

    const seen: string[] = [];
    await requestAssistantTurn([], "hi", [], "", (event) =>
      seen.push(event.type === "tool" ? `tool:${event.event.name}` : event.type),
    );

    expect(seen).toEqual(["start", "tool:get_portfolio", "tool:get_quote"]);
  });

  it("surfaces a truncated stream as an error and never as an empty bubble", async () => {
    fetchMock.mockResolvedValue(
      sseResponse([frame("start", {}), frame("tool", { name: "get_flow", input: {}, ok: true })]),
    );

    const turn = await requestAssistantTurn([], "How is SPY flow?");

    expect(turn.content.trim()).not.toBe("");
    expect(turn.content.toLowerCase()).toContain("did not finish");
    expect(turn.proposal).toBeNull();
    // The tool events that DID arrive are kept: the trace should not lose the
    // work the turn is known to have done.
    expect(turn.toolEvents).toHaveLength(1);
  });

  it("surfaces a mid-turn error frame", async () => {
    fetchMock.mockResolvedValue(
      sseResponse([frame("start", {}), frame("error", { error: "provider exploded" })]),
    );

    const turn = await requestAssistantTurn([], "How is SPY flow?");

    expect(turn.content).toContain("provider exploded");
  });

  it("frames split across chunk boundaries are still parsed", async () => {
    const whole = frame("start", {}) + frame("done", DONE);
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        const bytes = new TextEncoder().encode(whole);
        controller.enqueue(bytes.slice(0, 9));
        controller.enqueue(bytes.slice(9, 40));
        controller.enqueue(bytes.slice(40));
        controller.close();
      },
    });
    fetchMock.mockResolvedValue(
      new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );

    const turn = await requestAssistantTurn([], "hi");
    expect(turn.content).toContain("Flow is neutral.");
  });

  it("still reads a plain JSON body", async () => {
    // Pre-stream rejections and non-streaming harnesses keep the old shape.
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ content: "Read." }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const turn = await requestAssistantTurn([], "hi");
    expect(turn.content).toContain("Read.");
  });

  it("keeps the edge-timeout copy for a pre-stream 504", async () => {
    fetchMock.mockResolvedValue(new Response("<html>gateway timeout</html>", { status: 504 }));

    const turn = await requestAssistantTurn([], "hi");
    expect(turn.content).toContain("timed out");
  });
});
