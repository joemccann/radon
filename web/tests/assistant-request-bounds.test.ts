/** R-314 / REL-317: bounded input and request lifetime at the actual wire owner. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { requestAssistantTurn } from "@/lib/chat";
import { boundedAssistantHistory, textPayloadViolation } from "@/lib/assistant/requestBudget";

const success = () => new Response(JSON.stringify({ content: "recovered", proposal: null, toolEvents: [] }), {
  headers: { "Content-Type": "application/json" },
});

describe("assistant request bounds", () => {
  const fetchMock = vi.fn<typeof fetch>();
  beforeEach(() => { vi.useFakeTimers(); fetchMock.mockReset(); vi.stubGlobal("fetch", fetchMock); });
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

  it("keeps recent bounded history and the full current prompt on the exact wire", async () => {
    fetchMock.mockResolvedValue(success());
    const history = Array.from({ length: 50 }, (_, i) => ({ role: "assistant" as const, content: `turn-${i}:` + "x".repeat(5000) }));
    await requestAssistantTurn(history, "current prompt");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/assistant");
    expect(init?.method).toBe("POST");
    expect(init?.signal?.aborted).toBe(false);
    const messages = JSON.parse(init!.body as string).messages;
    expect(messages.length).toBeLessThanOrEqual(40);
    expect(messages.reduce((n: number, m: { content: string }) => n + new TextEncoder().encode(m.content).length, 0)).toBeLessThanOrEqual(128000);
    expect(messages.at(-1)).toEqual({ role: "user", content: "current prompt" });
    expect(messages.at(-2)).toEqual(history.at(-1));
    expect(messages[0]).not.toEqual(history[0]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("refuses an oversized multibyte current prompt before fetch", async () => {
    fetchMock.mockResolvedValue(success());
    const result = await requestAssistantTurn([], "字".repeat(12000));
    expect(result.failed).toBe(true);
    expect(result.proposal).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("bounds aggregate text blocks and preserves image evidence and source history", () => {
    const image = { type: "image" as const, source: { type: "base64" as const, media_type: "image/png" as const, data: "fixture" } };
    const current = { role: "user" as const, content: [{ type: "text" as const, text: "current" }, image] };
    const history = Array.from({ length: 50 }, () => ({ role: "assistant" as const, content: "prior" }));
    const before = JSON.stringify(history);
    const messages = boundedAssistantHistory(history, current);
    expect(messages).toHaveLength(40);
    expect(messages.at(-1)).toBe(current);
    expect(JSON.stringify(history)).toBe(before);
    expect(textPayloadViolation(messages)).toBeNull();
    expect(textPayloadViolation([{ content: [{ type: "text", text: "x".repeat(16001) }, { type: "text", text: "x".repeat(16000) }] }])?.status).toBe(413);
    expect(textPayloadViolation(Array.from({ length: 5 }, () => ({ content: "x".repeat(30000) })))?.status).toBe(413);
  });

  for (const phase of ["headers", "stream"] as const) {
    it(`bounds a stalled ${phase} and clears the owned timer`, async () => {
      fetchMock.mockImplementation(async (_url, init) => {
        const signal = init!.signal!;
        if (phase === "headers") return new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
        });
        return new Response(new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('event: start\ndata: {}\n\n'));
            signal.addEventListener("abort", () => controller.error(new DOMException("aborted", "AbortError")), { once: true });
          },
        }), { headers: { "Content-Type": "text/event-stream" } });
      });
      const pending = requestAssistantTurn([], "current prompt");
      await vi.advanceTimersByTimeAsync(330001);
      const result = await pending;
      expect(result.failed).toBe(true);
      expect(result.proposal).toBeNull();
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(fetchMock.mock.calls[0][1]!.signal!.aborted).toBe(true);
      expect(vi.getTimerCount()).toBe(0);
      fetchMock.mockResolvedValue(success());
      expect((await requestAssistantTurn([], "recovery")).content).toBe("recovered");
      expect(vi.getTimerCount()).toBe(0);
    });
  }

  it("settles a terminal frame without waiting for a stalled EOF and cancels its reader", async () => {
    const cancel = vi.fn();
    fetchMock.mockResolvedValue(new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('event: done\ndata: {"content":"complete","proposal":null,"toolEvents":[]}\n\n'));
      }, cancel,
    }), { headers: { "Content-Type": "text/event-stream" } }));
    expect((await requestAssistantTurn([], "current prompt")).content).toBe("complete");
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("preserves caller cancellation and refuses a caller already stopped", async () => {
    const caller = new AbortController();
    caller.abort();
    fetchMock.mockResolvedValue(success());
    await expect(requestAssistantTurn([], "current prompt", [], "", undefined, caller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });
});
