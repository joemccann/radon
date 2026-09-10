import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { parseSseFrames } from "./assistantStream";

/**
 * REL-161 (R-451, R-453): `call_api` is bounded by the principal and the turn.
 *
 * Wire-level: `fetch` is stubbed, the route handler (or the loop) is the
 * caller, and the assertion is the full FastAPI URL + method, or that no
 * request fired at all.
 *
 *   - a demo principal's `call_api {POST /portfolio/sync}` never reaches
 *     FastAPI: the Next twin (`/api/portfolio` POST) is operatorOnly and the
 *     catalog client mirrors that option.
 *   - a stream the client cancelled after round 1 issues no further FastAPI
 *     calls: the per-turn AbortSignal is checked before every tool call.
 *   - the read.spawn cap counts ATTEMPTS: two timed-out spawns exhaust it.
 */

const FASTAPI = "http://fastapi.test:8321";
const ENV_KEYS = ["ASSISTANT_MOCK", "RADON_API_URL", "RADON_SERVICE_TOKEN"];

type Principal = { userId: string; kind: "operator" | "demo"; token?: string };

function postRequest(body: unknown): Request {
  return new Request("http://localhost/api/assistant", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function toolUse(id: string, input: Record<string, unknown>) {
  return {
    provider: "anthropic",
    model: "mock-model",
    text: "",
    toolCalls: [{ id, name: "call_api", input }],
    usage: { inputTokens: 1, outputTokens: 1 },
    stopReason: "tool_use",
  };
}

const FINAL = {
  provider: "anthropic",
  model: "mock-model",
  text: "Done.",
  usage: { inputTokens: 1, outputTokens: 1 },
  stopReason: "end_turn",
};

function mockAccess(principal: Principal) {
  vi.doMock("@/lib/routeAccess", () => ({
    requireRouteAccess: vi.fn(async () => ({ ok: true, principal })),
  }));
  vi.doMock("@/lib/demo/enforceAiQuota", () => ({
    enforceDemoAiQuota: vi.fn(async () => null),
  }));
}

function fetchCalls(fetchSpy: ReturnType<typeof vi.fn>): Array<{ url: string; method: string }> {
  return fetchSpy.mock.calls.map((call) => {
    const [url, init] = call as unknown as [string, RequestInit | undefined];
    return { url: String(url), method: String(init?.method ?? "GET") };
  });
}

describe("assistant call_api bounds", () => {
  const saved: Record<string, string | undefined> = {};
  let fetchSpy: ReturnType<typeof vi.fn>;
  let dbExecute: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.resetModules();
    for (const key of ENV_KEYS) saved[key] = process.env[key];
    process.env.ASSISTANT_MOCK = "1";
    process.env.RADON_API_URL = FASTAPI;
    delete process.env.RADON_SERVICE_TOKEN;
    fetchSpy = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchSpy as unknown as typeof fetch);
    dbExecute = vi.fn().mockResolvedValue({ rows: [] });
    vi.doMock("@/lib/dbExecute", () => ({ dbExecute }));
    vi.spyOn(console, "log").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.doUnmock("@/lib/routeAccess");
    vi.doUnmock("@/lib/demo/enforceAiQuota");
    vi.doUnmock("@/lib/llm/provider");
    vi.doUnmock("@/lib/dbExecute");
    for (const key of ENV_KEYS) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  });

  async function runTurn(principal: Principal, input: Record<string, unknown>) {
    mockAccess(principal);
    const chat = vi.fn().mockResolvedValueOnce(toolUse("tu_1", input)).mockResolvedValue(FINAL);
    vi.doMock("@/lib/llm/provider", () => ({ chat }));
    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(postRequest({ messages: [{ role: "user", content: "sync my portfolio" }] }) as never);
    expect(res.status).toBe(200);
    return parseSseFrames(await res.text());
  }

  it("demo principal: call_api {POST /portfolio/sync} sends NOTHING to FastAPI", async () => {
    const frames = await runTurn(
      { userId: "user_demo", kind: "demo", token: "jwt-demo" },
      { method: "POST", path: "/portfolio/sync" },
    );

    expect(fetchCalls(fetchSpy)).toEqual([]);
    const tool = frames.find((frame) => frame.event === "tool")!.data as { ok: boolean; error?: string };
    expect(tool.ok).toBe(false);
    expect(tool.error).toMatch(/operator/i);
  });

  it("operator principal: the same call_api reaches POST {FASTAPI}/portfolio/sync", async () => {
    await runTurn(
      { userId: "user_operator", kind: "operator", token: "jwt-op" },
      { method: "POST", path: "/portfolio/sync" },
    );

    expect(fetchCalls(fetchSpy)).toEqual([{ url: `${FASTAPI}/portfolio/sync`, method: "POST" }]);
  });

  it("demo principal: read.spawn scans that have no operatorOnly twin still run", async () => {
    await runTurn(
      { userId: "user_demo", kind: "demo", token: "jwt-demo" },
      { method: "POST", path: "/gex/scan" },
    );

    expect(fetchCalls(fetchSpy)).toEqual([{ url: `${FASTAPI}/gex/scan`, method: "POST" }]);
  });

  it("a stream cancelled after round 1 issues no further FastAPI calls", async () => {
    mockAccess({ userId: "user_operator", kind: "operator", token: "jwt-op" });
    let releaseRound1!: () => void;
    const round1 = new Promise<void>((resolve) => {
      releaseRound1 = resolve;
    });
    const chat = vi
      .fn()
      .mockImplementationOnce(async () => {
        await round1;
        return toolUse("tu_1", { method: "GET", path: "/quote/AAPL" });
      })
      .mockResolvedValue(FINAL);
    vi.doMock("@/lib/llm/provider", () => ({ chat }));

    const { POST } = await import("@/app/api/assistant/route");
    const res = await POST(postRequest({ messages: [{ role: "user", content: "quote AAPL" }] }) as never);
    const reader = res.body!.getReader();
    expect(new TextDecoder().decode((await reader.read()).value)).toContain("event: start");

    // The operator closes the tab while the model is still thinking.
    await reader.cancel();
    releaseRound1();

    // The turn settles when telemetry lands; only then is the fetch log final.
    await vi.waitFor(() => expect(dbExecute).toHaveBeenCalledTimes(1));

    expect(fetchCalls(fetchSpy)).toEqual([]);
    expect(chat).toHaveBeenCalledTimes(1);
    const [stmt] = dbExecute.mock.calls[0];
    expect(stmt.args[5]).toBe("cancelled");
  });

  it("the wall clock aborts a turn whose loop never settles", async () => {
    vi.useFakeTimers();
    mockAccess({ userId: "user_operator", kind: "operator", token: "jwt-op" });
    const runAssistantLoop = vi.fn(
      (_turns: unknown, _system: unknown, _principal: unknown, _selection: unknown, _onTool: unknown, signal: AbortSignal) =>
        new Promise((resolve) => {
          signal.addEventListener("abort", () =>
            resolve({
              content: "Turn cancelled before it finished.",
              model: "mock",
              provider: "anthropic",
              toolEvents: [],
              rounds: 3,
              usage: { inputTokens: 1, outputTokens: 1 },
              outcome: "cancelled",
            }),
          );
        }),
    );
    vi.doMock("@/lib/assistant/loop", async () => {
      const actual = await vi.importActual<typeof import("@/lib/assistant/loop")>("@/lib/assistant/loop");
      return { ...actual, runAssistantLoop };
    });

    const { POST, ASSISTANT_TURN_WALL_CLOCK_MS } = await import("@/app/api/assistant/route");
    const res = await POST(postRequest({ messages: [{ role: "user", content: "scan everything" }] }) as never);
    const drained = res.text();
    await vi.advanceTimersByTimeAsync(ASSISTANT_TURN_WALL_CLOCK_MS + 1);
    const frames = parseSseFrames(await drained);
    vi.doUnmock("@/lib/assistant/loop");
    vi.useRealTimers();

    expect(runAssistantLoop).toHaveBeenCalledTimes(1);
    const done = frames.find((frame) => frame.event === "done")!.data as { content: string };
    expect(done.content).toBe("Turn cancelled before it finished.");
  });

  it("read.spawn cap counts attempts: two timed-out spawns exhaust it", async () => {
    fetchSpy.mockRejectedValue(new DOMException("timed out", "TimeoutError"));
    const { createAssistantTurnBudget, executeTool } = await import("@/lib/assistant/tools");
    const principal = { userId: "user_operator", kind: "operator" as const, token: "jwt-op" };
    const budget = createAssistantTurnBudget();

    const first = await executeTool("call_api", { method: "POST", path: "/scan" }, principal, budget);
    const second = await executeTool("call_api", { method: "POST", path: "/discover" }, principal, budget);
    const third = await executeTool("call_api", { method: "POST", path: "/gex/scan" }, principal, budget);

    expect(first.ok).toBe(false);
    expect(second.ok).toBe(false);
    expect(third.ok).toBe(false);
    expect(third.error).toMatch(/spawn/i);
    expect(fetchCalls(fetchSpy)).toEqual([
      { url: `${FASTAPI}/scan`, method: "POST" },
      { url: `${FASTAPI}/discover`, method: "POST" },
    ]);
  });

  it("call_api forwards the turn signal to radonFetch and refuses once it is aborted", async () => {
    const { createAssistantTurnBudget, executeTool } = await import("@/lib/assistant/tools");
    const principal = { userId: "user_operator", kind: "operator" as const, token: "jwt-op" };
    const controller = new AbortController();
    const budget = createAssistantTurnBudget(controller.signal);

    const live = await executeTool("call_api", { method: "GET", path: "/quote/AAPL" }, principal, budget);
    expect(live.ok).toBe(true);
    const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.signal).toBeInstanceOf(AbortSignal);

    controller.abort();
    const dead = await executeTool("call_api", { method: "GET", path: "/quote/MSFT" }, principal, budget);
    expect(dead.ok).toBe(false);
    expect(fetchCalls(fetchSpy)).toEqual([{ url: `${FASTAPI}/quote/AAPL`, method: "GET" }]);
  });
});
