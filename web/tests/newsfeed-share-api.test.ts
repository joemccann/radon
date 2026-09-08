import { beforeEach, describe, expect, it, vi } from "vitest";
const { guard, chat } = vi.hoisted(() => ({ guard: vi.fn(), chat: vi.fn() }));
vi.mock("../lib/routeAccess", () => ({ requireRouteAccess: guard }));
vi.mock("../lib/llm/provider", () => ({ chat }));
import { POST } from "../app/api/newsfeed/share/route";
const input = { title: "Seasonality", content: "Median rises from 104 to 130." };
const request = (value: unknown = input, signal?: AbortSignal) => new Request("http://localhost/api/newsfeed/share", { method: "POST", body: JSON.stringify(value), signal });
beforeEach(() => {
  vi.clearAllMocks();
  guard.mockResolvedValue({ ok: true, principal: { userId: "joe", kind: "operator" } });
  chat.mockResolvedValue({ text: JSON.stringify({ title: "Seasonality", content: "104 to 130. That is the historical path." }) });
});
describe("POST newsfeed/share", () => {
  it.each([401, 403, 429])("preserves access rejection %s without model calls", async status => {
    guard.mockResolvedValue({ ok: false, response: new Response(null, { status }) });
    expect((await POST(request())).status).toBe(status);
    expect(chat).not.toHaveBeenCalled();
  });
  it.each([null, {}, { ...input, title: "" }, { ...input, content: 1 }, { ...input, content: "x".repeat(12001) }])("rejects invalid input", async value => {
    expect((await POST(request(value))).status).toBe(400);
    expect(chat).not.toHaveBeenCalled();
  });
  it("rewrites sanitized source data with bounded operator-only access", async () => {
    const response = await POST(request({ ...input, content: input.content + " Source: ZeroHedge https://zerohedge.com/a" }));
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ title: "Seasonality", content: "104 to 130. That is the historical path.", caption: "Seasonality\n\n104 to 130. That is the historical path." });
    expect(guard).toHaveBeenCalledWith(expect.any(Request), expect.objectContaining({ operatorOnly: true, durableRateTier: "D", rate: expect.objectContaining({ limit: 10 }) }));
    expect(chat.mock.calls[0][0].messages[0].content).not.toMatch(/zerohedge/i);
    expect(chat.mock.calls[0][0].signal).toBeInstanceOf(AbortSignal);
  });
  it("does not expose provider errors", async () => {
    chat.mockRejectedValue(new Error("secret upstream response"));
    const response = await POST(request());
    expect(response.status).toBe(502);
    expect(await response.text()).not.toContain("secret");
  });
  it("rejects model hallucinated numbers", async () => {
    chat.mockResolvedValue({ text: '{"title":"Seasonality","content":"Up 25%."}' });
    expect((await POST(request())).status).toBe(502);
  });
  it("reports timeouts safely", async () => {
    chat.mockRejectedValue(new DOMException("timeout", "TimeoutError"));
    expect((await POST(request())).status).toBe(504);
  });
  it("passes client cancellation to the provider", async () => {
    const controller = new AbortController();
    controller.abort();
    chat.mockImplementation(({ signal }) => { signal.throwIfAborted(); });
    expect((await POST(request(input, controller.signal))).status).toBe(499);
  });
});
