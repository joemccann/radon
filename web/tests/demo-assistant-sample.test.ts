/**
 * @vitest-environment node
 *
 * 2026-10-27: the demo deployment holds no LLM subscription grant, and the
 * operator's grants must not serve anonymous trial users, so every demo
 * assistant turn failed with "Missing Anthropic subscription". Demo principals
 * now get a request-time sample reply in the same SSE shape. No model, no
 * quota charge, no telemetry row. Operators keep the real loop.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const mocks = vi.hoisted(() => ({
  requireRouteAccess: vi.fn(),
  runAssistantLoop: vi.fn(),
  enforceDemoAiQuota: vi.fn(),
  recordAssistantTurn: vi.fn(),
}));

vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess: mocks.requireRouteAccess }));
vi.mock("@/lib/assistant/loop", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  runAssistantLoop: mocks.runAssistantLoop,
}));
vi.mock("@/lib/demo/enforceAiQuota", () => ({ enforceDemoAiQuota: mocks.enforceDemoAiQuota }));
vi.mock("@/lib/assistant/telemetry", () => ({ recordAssistantTurn: mocks.recordAssistantTurn }));

function as(kind: "demo" | "operator"): void {
  mocks.requireRouteAccess.mockResolvedValue({ ok: true, principal: { userId: `${kind}-user`, kind } });
}

function post(text: string): NextRequest {
  return new NextRequest("http://localhost/api/assistant", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messages: [{ role: "user", content: text }] }),
  });
}

function frames(body: string): Array<{ event: string; data: Record<string, unknown> }> {
  return body
    .split("\n\n")
    .filter(Boolean)
    .map((chunk) => {
      const [eventLine, dataLine] = chunk.split("\n");
      return { event: eventLine.replace("event: ", ""), data: JSON.parse(dataLine.replace("data: ", "")) };
    });
}

beforeEach(() => {
  vi.resetModules();
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.enforceDemoAiQuota.mockResolvedValue(null);
  mocks.runAssistantLoop.mockRejectedValue(new Error("Missing Anthropic subscription"));
});

describe("demo assistant sample responder", () => {
  it("streams a labelled sample reply without a model, quota charge or telemetry row", async () => {
    as("demo");
    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(post("analyze NVDA"));
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/event-stream");
    const events = frames(await res.text());
    expect(events.map((e) => e.event)).toEqual(["start", "done"]);
    const done = events[1].data;
    expect(done.model).toBe("radon-demo-sample");
    expect(String(done.content)).toContain("NVDA");
    expect(String(done.content)).toMatch(/sample/i);
    expect(done.proposal).toBeNull();
    expect(mocks.runAssistantLoop).not.toHaveBeenCalled();
    expect(mocks.enforceDemoAiQuota).not.toHaveBeenCalled();
    expect(mocks.recordAssistantTurn).not.toHaveBeenCalled();
  });

  it("answers a question with no ticker", async () => {
    as("demo");
    const { POST } = await import("@/app/api/assistant/route");
    const events = frames(await (await POST(post("what can you do?"))).text());
    expect(events.at(-1)?.event).toBe("done");
    expect(String(events.at(-1)?.data.content).length).toBeGreaterThan(40);
  });

  it("keeps request validation for demo principals", async () => {
    as("demo");
    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(
      new NextRequest("http://localhost/api/assistant", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ messages: [] }),
      }),
    );
    expect(res.status).toBe(400);
  });

  it("operators still run the real assistant loop", async () => {
    as("operator");
    mocks.runAssistantLoop.mockResolvedValue({
      content: "real answer",
      model: "claude-x",
      rounds: 1,
      toolEvents: [],
      usage: { inputTokens: 1, outputTokens: 1 },
      outcome: "ok",
    });
    const { POST } = await import("@/app/api/assistant/route");
    const events = frames(await (await POST(post("analyze NVDA"))).text());
    expect(mocks.runAssistantLoop).toHaveBeenCalledTimes(1);
    expect(events.at(-1)?.data.content).toBe("real answer");
  });
});
