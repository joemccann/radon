import { beforeEach, describe, it, expect, vi } from "vitest";
const access = vi.hoisted(() => vi.fn());
const upstream = vi.hoisted(() => vi.fn());
vi.mock("../lib/routeAccess", () => ({ requireRouteAccess: access }));
vi.mock("../lib/radonApi", async importOriginal => ({ ...await importOriginal<object>(), radonFetchResponse: upstream }));
import { GET } from "../app/api/newsfeed/research/files/[asset]/route";
const asset = "a".repeat(64) + ".png";
const request = new Request("http://localhost/api/newsfeed/research/files/" + asset);
beforeEach(() => { vi.clearAllMocks(); access.mockResolvedValue({ok: true, principal: {kind: "operator", token: "test-only"}}); });
describe("private research files", () => {
  it("requires operator access before fetching bytes", async () => { access.mockResolvedValue({ok: false, response: new Response(null, {status:403})}); expect((await GET(request, {params: Promise.resolve({asset})})).status).toBe(403); expect(upstream).not.toHaveBeenCalled(); expect(access).toHaveBeenCalledWith(request, {operatorOnly:true}); });
  it("rejects traversal and arbitrary paths", async () => { expect((await GET(request, {params:Promise.resolve({asset:"../secret.pdf"})})).status).toBe(404); expect(upstream).not.toHaveBeenCalled(); });
  it("streams original bytes without public caching", async () => { upstream.mockResolvedValue(new Response(new Uint8Array([137,80,78,71]))); const response = await GET(request, {params:Promise.resolve({asset})}); expect([...new Uint8Array(await response.arrayBuffer())]).toEqual([137,80,78,71]); expect(response.headers.get("Cache-Control")).toBe("private, no-store"); expect(response.headers.get("Content-Type")).toBe("image/png"); expect(upstream.mock.calls[0][1]).toMatchObject({token:"test-only",cache:"no-store"}); });
});
