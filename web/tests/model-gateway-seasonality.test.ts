import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const chat = vi.hoisted(() => vi.fn());
vi.mock("@/lib/llm/provider", () => ({ chat }));
vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess: async () => ({ ok: true, principal: { kind: "operator", userId: "operator" } }) }));
vi.mock("@/lib/demo/enforceAiQuota", () => ({ enforceDemoAiQuota: async () => null }));
vi.mock("@/lib/uwCountedFetch", () => ({ countedUwFetch: async () => new Response(JSON.stringify({ data: [] }), { headers: { "Content-Type": "application/json" } }) }));
vi.mock("fs", () => ({ promises: { readFile: async () => { throw new Error("ENOENT"); }, mkdir: async () => {}, writeFile: async () => {} } }));
const months = Array.from({ length: 12 }, (_, index) => ({ month: index + 1, years: 20, avg_change: 0.1, median_change: 0.1, max_change: 0.2, min_change: -0.1, positive_months_perc: 0.6 }));
beforeEach(() => { chat.mockReset(); });
afterEach(() => vi.unstubAllGlobals());
async function call() { const { GET } = await import("@/app/api/ticker/seasonality/route"); return GET(new Request("http://localhost/api/ticker/seasonality?ticker=AAPL")); }
describe("seasonality uses the common multimodal service", () => {
  it("submits bounded fixed-host image bytes without provider credentials or model pins", async () => {
    const fetch = vi.fn(async () => new Response(new Uint8Array([137, 80, 78, 71]), { headers: { "Content-Type": "image/png" } }));
    vi.stubGlobal("fetch", fetch);
    chat.mockResolvedValue({ text: JSON.stringify(months), provider: "anthropic", model: "vision" });
    const response = await call();
    await expect(response.json()).resolves.toMatchObject({ source: "equityclock", data: months });
    expect(fetch).toHaveBeenCalledWith("https://charts.equityclock.com/seasonal_charts/AAPL_sheet.png", expect.objectContaining({ redirect: "error", cache: "no-store", signal: expect.any(AbortSignal) }));
    const request = chat.mock.calls[0][0];
    expect(request.messages[0].content[0]).toEqual({ type: "image", source: { type: "base64", media_type: "image/png", data: "iVBORw==" } });
    expect(request).not.toHaveProperty("provider");
    expect(request).not.toHaveProperty("model");
  });
  it("rejects declared oversized images before model work", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("x", { headers: { "Content-Type": "image/png", "Content-Length": String(6 * 1024 * 1024) } })));
    expect((await call()).status).toBe(200);
    expect(chat).not.toHaveBeenCalled();
  });
  it("bounds streaming images even when the size header is absent", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new Uint8Array(5 * 1024 * 1024 + 1), { headers: { "Content-Type": "image/png" } })));
    await call(); expect(chat).not.toHaveBeenCalled();
  });
  it("rejects non-image responses without spending a model request", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("login", { headers: { "Content-Type": "text/html" } })));
    await call(); expect(chat).not.toHaveBeenCalled();
  });
});
