import { beforeEach, describe, expect, it, vi } from "vitest";

const { guard, explainNewsfeedPost, enforceDemoAiQuota } = vi.hoisted(() => ({
  guard: vi.fn(),
  explainNewsfeedPost: vi.fn(),
  enforceDemoAiQuota: vi.fn(),
}));

vi.mock("../lib/routeAccess", () => ({ requireRouteAccess: guard }));
vi.mock("../lib/newsfeedExplainGenerate", () => ({ explainNewsfeedPost }));
vi.mock("../lib/demo/enforceAiQuota", () => ({ enforceDemoAiQuota }));

import { POST, radonCapability } from "../app/api/newsfeed/explain/route";
import { EXPLAIN_BUDGET_MS } from "../lib/newsfeedExplainProgress";

const input = {
  postId: "pjm-2028-capacity",
  title: "PJM capacity auction cleared at the cap",
  content: "The 2028/2029 auction cleared at $325/MW-day and came up 6.8 GW short.",
};
const result = {
  explanation: "PJM paid plants to stay available. A capacity auction buys that standby power.",
  cached: false,
  thin: false,
  model: "claude-opus-5",
  provider: "anthropic",
  hasImage: false,
};

const request = (value: unknown = input, signal?: AbortSignal) =>
  new Request("http://localhost/api/newsfeed/explain", {
    method: "POST",
    body: JSON.stringify(value),
    signal,
  });

type SseEvent = { event: string; data: Record<string, unknown> };
function parseEvents(text: string): SseEvent[] {
  return text.split("\n\n").filter(Boolean).map((frame) => {
    const event = frame.match(/^event: (.+)$/m)?.[1] ?? "";
    const data = JSON.parse(frame.match(/^data: (.+)$/m)?.[1] ?? "{}") as Record<string, unknown>;
    return { event, data };
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  enforceDemoAiQuota.mockResolvedValue(null);
  guard.mockResolvedValue({ ok: true, principal: { userId: "joe", kind: "operator" } });
  explainNewsfeedPost.mockImplementation(async (_input, { onStage }) => {
    onStage("loading");
    onStage("drafting");
    onStage("checking");
    return result;
  });
});

describe("POST /api/newsfeed/explain", () => {
  it("is internal so chat cannot spend the subscription", () => {
    expect(radonCapability).toBe("internal");
  });

  it.each([401, 403, 429])("preserves access rejection %s without model calls", async (status) => {
    guard.mockResolvedValue({ ok: false, response: new Response(null, { status }) });
    expect((await POST(request())).status).toBe(status);
    expect(explainNewsfeedPost).not.toHaveBeenCalled();
  });

  it("returns the demo AI quota rejection before any model call", async () => {
    enforceDemoAiQuota.mockResolvedValue(new Response(null, { status: 429 }));
    expect((await POST(request())).status).toBe(429);
    expect(enforceDemoAiQuota).toHaveBeenCalledWith("newsfeed/explain");
    expect(explainNewsfeedPost).not.toHaveBeenCalled();
  });

  it.each([
    null,
    {},
    { ...input, postId: "" },
    { ...input, title: "" },
    { ...input, content: 1 },
    { ...input, content: "x".repeat(12_001) },
  ])("rejects invalid input", async (value) => {
    expect((await POST(request(value))).status).toBe(400);
    expect(explainNewsfeedPost).not.toHaveBeenCalled();
  });

  it("streams stages then the explanation behind authenticated rate limits", async () => {
    const response = await POST(request());
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("text/event-stream; charset=utf-8");
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(parseEvents(await response.text())).toEqual([
      { event: "stage", data: { stage: "queued", budgetMs: EXPLAIN_BUDGET_MS } },
      { event: "stage", data: { stage: "loading" } },
      { event: "stage", data: { stage: "drafting" } },
      { event: "stage", data: { stage: "checking" } },
      { event: "result", data: result },
    ]);
    expect(guard).toHaveBeenCalledWith(
      expect.any(Request),
      expect.objectContaining({
        operatorOnly: false,
        durableRateTier: "D",
        rate: expect.objectContaining({ key: "newsfeed-explain", limit: 8 }),
      }),
    );
  });

  it("does not expose provider errors or mention cerebras", async () => {
    const logged: string[] = [];
    vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
      logged.push(args.map(String).join(" "));
    });
    // The SSE body already hides this. The server log is the other copy:
    // failureEvent interpolates error.message, and chat() builds that message
    // from the raw upstream body (provider.ts readErrorDetail).
    const leaked = "sk-ant-api03-abcdefghij";
    const dbUrl = "libsql://radon-joemccann.aws-us-west-2.turso.io";
    explainNewsfeedPost.mockRejectedValue(
      new Error(`CEREBRAS_API_KEY secret upstream ${leaked} ${dbUrl}`),
    );
    const text = await (await POST(request())).text();
    expect(text).not.toContain("secret");
    expect(text).not.toContain("CEREBRAS");
    expect(text).not.toContain(leaked);
    expect(text).not.toContain(dbUrl);
    const logText = logged.join("\n");
    expect(logText.length).toBeGreaterThan(0);
    expect(logText).not.toContain(leaked);
    expect(logText).not.toContain(dbUrl);
    expect(logText).toContain("[redacted-key]");
    expect(logText).toContain("[redacted-db-url]");
    expect(parseEvents(text).at(-1)).toEqual({
      event: "error",
      data: { error: "Could not explain this post. Try again.", status: 502 },
    });
  });

  it("reports timeouts safely", async () => {
    explainNewsfeedPost.mockRejectedValue(new DOMException("timeout", "TimeoutError"));
    expect(parseEvents(await (await POST(request())).text()).at(-1)).toEqual({
      event: "error",
      data: { error: "Explanation timed out. Try again.", status: 504 },
    });
  });
});
