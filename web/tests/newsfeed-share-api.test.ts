import { beforeEach, describe, expect, it, vi } from "vitest";

const { guard, chat } = vi.hoisted(() => ({ guard: vi.fn(), chat: vi.fn() }));
vi.mock("../lib/routeAccess", () => ({ requireRouteAccess: guard }));
vi.mock("../lib/llm/provider", () => ({ chat, resolveProvider: () => "anthropic", alternateProvider: () => null }));
import { POST } from "../app/api/newsfeed/share/route";

const request = (body = JSON.stringify({ title: "Seasonality", content: "Median rises from 104 to 130." })) =>
  new Request("http://localhost/api/newsfeed/share", { method: "POST", body });

beforeEach(() => {
  vi.clearAllMocks();
  guard.mockResolvedValue({ ok: true, principal: { userId: "joe", kind: "operator" } });
  chat.mockRejectedValue(new Error("Retired rewriting must never call a model"));
});

describe("retired POST newsfeed/share", () => {
  it.each([401, 403, 429])("preserves access rejection %s without model calls", async status => {
    const rejection = new Response(null, { status });
    guard.mockResolvedValue({ ok: false, response: rejection });
    expect(await POST(request())).toBe(rejection);
    expect(chat).not.toHaveBeenCalled();
  });

  it("returns a permanent JSON retirement response without streaming or model calls", async () => {
    const response = await POST(request());
    expect(response.status).toBe(410);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("content-type")).toContain("application/json");
    expect(await response.json()).toEqual({ error: "Newsfeed share rewriting has been retired." });
    expect(chat).not.toHaveBeenCalled();
    expect(guard).toHaveBeenCalledWith(expect.any(Request), {
      operatorOnly: true,
      rate: { key: "newsfeed-share", limit: 10, windowMs: 60_000 },
      durableRateTier: "D",
    });
  });

  it.each(["not JSON", "", "x".repeat(80_001)])("does not parse or process retired rewrite bodies", async body => {
    const incoming = request(body);
    const read = vi.spyOn(incoming, "text");
    expect((await POST(incoming)).status).toBe(410);
    expect(read).not.toHaveBeenCalled();
    expect(chat).not.toHaveBeenCalled();
  });
});
