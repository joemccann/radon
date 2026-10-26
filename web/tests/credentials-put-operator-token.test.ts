/**
 * @vitest-environment node
 *
 * The app host requires an operator JWT on the Gateway password PUT even from
 * loopback, so the Next proxy must forward the operator's Clerk token on the
 * wire. Asserted at fetch: full URL, method and Authorization header.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const access = vi.fn();

vi.mock("@/lib/routeAccess", () => ({
  requireRouteAccess: (...args: unknown[]) => access(...args),
}));

vi.mock("@/lib/setup/envFiles", () => ({
  applyLiveWebCredentials: vi.fn(async () => undefined),
}));

const fetchMock = vi.fn();

beforeEach(() => {
  vi.resetModules();
  access.mockReset();
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(
    new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function put(service: string) {
  return new Request(`http://localhost/api/credentials/${service}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values: { TWS_PASSWORD: "synthetic-not-real" } }),
  });
}

describe("PUT /api/credentials/[service] operator token", () => {
  it("forwards the operator token to FastAPI", async () => {
    access.mockResolvedValue({
      ok: true,
      principal: { userId: "user_op", kind: "operator", token: "synthetic-operator-jwt" },
    });
    const { PUT } = await import("../app/api/credentials/[service]/route");

    const res = await PUT(put("ib_gateway") as never, {
      params: Promise.resolve({ service: "ib_gateway" }),
    });

    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8321/credentials/ib_gateway");
    expect(init.method).toBe("PUT");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer synthetic-operator-jwt");
    expect(JSON.parse(String(init.body))).toEqual({
      values: { TWS_PASSWORD: "synthetic-not-real" },
      updated_by: "user_op",
    });
  });

  it("sends nothing when route access is refused", async () => {
    access.mockResolvedValue({ ok: false, response: new Response(null, { status: 403 }) });
    const { PUT } = await import("../app/api/credentials/[service]/route");

    const res = await PUT(put("ib_gateway") as never, {
      params: Promise.resolve({ service: "ib_gateway" }),
    });

    expect(res.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
