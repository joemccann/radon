import { beforeEach, describe, expect, it, vi } from "vitest";
const { guard, rewriteInVoice } = vi.hoisted(() => ({ guard: vi.fn(), rewriteInVoice: vi.fn() }));
vi.mock("../lib/routeAccess", () => ({ requireRouteAccess: guard }));
vi.mock("../lib/newsfeedVoiceRewrite", () => ({ rewriteInVoice }));
import { POST } from "../app/api/newsfeed/share/route";
import { VOICE_REWRITE_BUDGET_MS } from "../lib/newsfeedVoiceProgress";
const input = { title: "Seasonality", content: "Median rises from 104 to 130." };
const copy = { title: "Seasonality", content: "104 to 130.", caption: "Seasonality\n\n104 to 130." };
const request = (value: unknown = input, signal?: AbortSignal) => new Request("http://localhost/api/newsfeed/share", { method: "POST", body: JSON.stringify(value), signal });

type SseEvent = { event: string; data: Record<string, unknown> };
function parseEvents(text: string): SseEvent[] {
  return text.split("\n\n").filter(Boolean).map(frame => {
    const event = frame.match(/^event: (.+)$/m)?.[1] ?? "";
    const data = JSON.parse(frame.match(/^data: (.+)$/m)?.[1] ?? "{}") as Record<string, unknown>;
    return { event, data };
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  guard.mockResolvedValue({ ok: true, principal: { userId: "joe", kind: "operator" } });
  rewriteInVoice.mockImplementation(async (_input, { onStage }) => { onStage("drafting"); onStage("checking"); return copy; });
});

describe("POST newsfeed/share", () => {
  it.each([401, 403, 429])("preserves access rejection %s without model calls", async status => {
    guard.mockResolvedValue({ ok: false, response: new Response(null, { status }) });
    expect((await POST(request())).status).toBe(status);
    expect(rewriteInVoice).not.toHaveBeenCalled();
  });

  it.each([null, {}, { ...input, title: "" }, { ...input, content: 1 }, { ...input, content: "x".repeat(12001) }])("rejects invalid input", async value => {
    expect((await POST(request(value))).status).toBe(400);
    expect(rewriteInVoice).not.toHaveBeenCalled();
  });

  it("streams stage events then the verified draft, with bounded operator-only access", async () => {
    const response = await POST(request({ ...input, content: input.content + " Source: ZeroHedge https://zerohedge.com/a" }));
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("text/event-stream; charset=utf-8");
    expect(response.headers.get("cache-control")).toBe("no-store");
    const events = parseEvents(await response.text());
    expect(events).toEqual([
      { event: "stage", data: { stage: "queued", budgetMs: VOICE_REWRITE_BUDGET_MS } },
      { event: "stage", data: { stage: "drafting" } },
      { event: "stage", data: { stage: "checking" } },
      { event: "result", data: copy },
    ]);
    expect(guard).toHaveBeenCalledWith(expect.any(Request), expect.objectContaining({ operatorOnly: true, durableRateTier: "D", rate: expect.objectContaining({ limit: 10 }) }));
    const [sent, options] = rewriteInVoice.mock.calls[0];
    expect(sent.content).not.toMatch(/zerohedge/i);
    expect(options.signal).toBeInstanceOf(AbortSignal);
  });

  it("gives the rewrite the extended budget, well past the old 25s", async () => {
    const expired = new AbortController();
    const timeout = vi.spyOn(AbortSignal, "timeout").mockReturnValue(expired.signal);
    let signal: AbortSignal | undefined;
    rewriteInVoice.mockImplementation((_input, options) => { signal = options.signal; return new Promise(() => {}); });
    await POST(request());
    await vi.waitFor(() => expect(signal).toBeDefined());
    expect(timeout).toHaveBeenCalledWith(VOICE_REWRITE_BUDGET_MS);
    expect(VOICE_REWRITE_BUDGET_MS).toBeGreaterThanOrEqual(90_000);
    expect(signal?.aborted).toBe(false);
    expired.abort();
    expect(signal?.aborted).toBe(true);
    timeout.mockRestore();
  });

  it("does not expose provider errors", async () => {
    rewriteInVoice.mockRejectedValue(new Error("secret upstream response"));
    const text = await (await POST(request())).text();
    expect(text).not.toContain("secret");
    expect(parseEvents(text).at(-1)).toEqual({ event: "error", data: { error: "Could not generate a verified draft. Try again.", status: 502 } });
  });

  it("reports timeouts safely", async () => {
    rewriteInVoice.mockRejectedValue(new DOMException("timeout", "TimeoutError"));
    const events = parseEvents(await (await POST(request())).text());
    expect(events.at(-1)).toEqual({ event: "error", data: { error: "Rewrite timed out. Try again.", status: 504 } });
  });

  it("passes client cancellation to the rewrite and ends the stream quietly", async () => {
    const controller = new AbortController();
    rewriteInVoice.mockImplementation((_input, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(signal.reason));
    }));
    const response = await POST(request(input, controller.signal));
    controller.abort();
    const events = parseEvents(await response.text());
    expect(events.map(event => event.event)).toEqual(["stage"]);
  });
});
