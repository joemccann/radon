import { beforeEach, describe, it, expect, vi } from "vitest";
const access = vi.hoisted(() => vi.fn());
const upstream = vi.hoisted(() => vi.fn());
vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess: access }));
vi.mock("@/lib/radonApi", async original => ({ ...await original<object>(), radonFetch: upstream }));
import { GET } from "@/app/api/research/evidence/[asset]/route";
const asset = "a".repeat(64) + ".json";
const params = { params: Promise.resolve({ asset }) };
beforeEach(() => { vi.clearAllMocks(); access.mockResolvedValue({ ok: true, principal: { token: "caller" } }); });
describe("agent-readable PDF evidence", () => {
  it("enforces operator identity before retrieval", async () => {
    access.mockResolvedValue({ ok: false, response: new Response(null, { status: 403 }) });
    expect((await GET(new Request("https://app.radon.run/api/research/evidence/"+asset), params)).status).toBe(403);
    expect(upstream).not.toHaveBeenCalled();
  });
  it("refuses traversal and overlong/empty queries", async () => {
    const request = new Request("https://app.radon.run/api/research/evidence/"+asset);
    expect((await GET(request, { params: Promise.resolve({ asset: "../secret.json" }) })).status).toBe(404);
    for (const query of ["", "a".repeat(201)]) {
      expect((await GET(new Request(request.url+"?query="+query), params)).status).toBe(400);
    }
    expect(upstream).not.toHaveBeenCalled();
  });
  it("forwards caller token and encoded literal query without caching", async () => {
    upstream.mockResolvedValue({ results: [{ text: "Capex -12.5%", page_number: 2 }] });
    const response = await GET(new Request("https://app.radon.run/api/research/evidence/"+asset+"?query=capex%20%26%20guidance"), params);
    expect(upstream).toHaveBeenCalledWith("/newsfeed/research/evidence/"+asset+"?query=capex%20%26%20guidance", expect.objectContaining({ token: "caller", cache: "no-store" }));
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect((await response.json()).results[0].text).toBe("Capex -12.5%");
  });
});
