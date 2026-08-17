/**
 * Runtime authorization matrix.
 *
 * `route-local-authz-matrix.test.ts` proves the guard is PRESENT in each route
 * file. It cannot prove the guard RUNS, that it runs on every exported method,
 * or that it runs before the privileged work — all three are grep-invisible.
 * This file imports each route module, invokes every exported HTTP method with
 * `requireRouteAccess` denying, and asserts the handler returned the deny
 * response without reaching its downstream effect.
 *
 * The options object is read off `mock.calls[0][1]`, so a comment containing
 * `operatorOnly: true` cannot satisfy it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockRequireRouteAccess = vi.fn();
vi.mock("@/lib/routeAccess", () => ({
  requireRouteAccess: mockRequireRouteAccess,
}));

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({
  radonFetch: mockRadonFetch,
  RadonApiError: class RadonApiError extends Error {
    constructor(
      readonly status: number,
      readonly detail: unknown,
    ) {
      super(`Radon API ${status}`);
      this.name = "RadonApiError";
    }
  },
}));

const mockReadOrdersSnapshotFromDb = vi.fn();
vi.mock("@/lib/orders/readOrdersFromDb", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  readOrdersSnapshotFromDb: mockReadOrdersSnapshotFromDb,
}));

const DENY_STATUS = 403;

function denyResponse(): { ok: false; response: Response } {
  return {
    ok: false,
    response: new Response(JSON.stringify({ error: { message: "Forbidden", status: DENY_STATUS } }), {
      status: DENY_STATUS,
      headers: { "content-type": "application/json" },
    }),
  };
}

/**
 * Every protected route reported by the security audit, with the segment
 * params its handlers receive. Kept in the same order as the source-grep
 * matrix so the two files stay comparable.
 */
const ROUTES: ReadonlyArray<{ path: string; params?: Record<string, string>; ext?: string }> = [
  { path: "assistant" },
  { path: "attribution" },
  { path: "backtest/[strategy]", params: { strategy: "momentum" } },
  { path: "blotter" },
  { path: "breadth" },
  { path: "cash-flows" },
  { path: "discover" },
  { path: "flow-analysis" },
  { path: "flow-analysis/[ticker]", params: { ticker: "SPY" } },
  { path: "futures/chain" },
  { path: "gamma-rotation" },
  { path: "gex" },
  { path: "index-options/chain" },
  { path: "internals" },
  { path: "journal" },
  { path: "journal/sync" },
  { path: "knowledge/prior-evals" },
  { path: "knowledge/search" },
  { path: "leap" },
  { path: "leap/scan" },
  { path: "menthorq/[command]/image", params: { command: "cta" }, ext: "tsx" },
  { path: "menthorq/cta" },
  { path: "menthorq/cta/image", ext: "tsx" },
  { path: "newsfeed/posts" },
  { path: "options/chain" },
  { path: "options/expirations" },
  { path: "options/exposure" },
  { path: "options/rv-ratio" },
  { path: "orders" },
  { path: "orders/cancel" },
  { path: "orders/modify" },
  { path: "orders/place" },
  { path: "orders/whatif" },
  { path: "paper/place" },
  { path: "performance" },
  { path: "pi" },
  { path: "portfolio" },
  { path: "preferences" },
  { path: "previous-close" },
  { path: "regime" },
  { path: "scanner" },
  { path: "scanner/strength" },
  { path: "scanner/strength/scan" },
  { path: "scanner/theta" },
  { path: "scanner/theta/scan" },
  { path: "short-availability/[ticker]", params: { ticker: "SPY" } },
  { path: "ticker/info" },
  { path: "ticker/news" },
  { path: "ticker/ratings" },
  { path: "ticker/seasonality" },
  { path: "vcg" },
  { path: "workflow/run" },
  { path: "service-health" },
];

const ADMIN_ROUTES = ["edge-health", "health", "host-metrics", "reliability", "slo"] as const;
const SHARE_ROUTES = ["gex", "internals", "menthorq/cta", "regime", "vcg"] as const;

/**
 * The destructive operator control plane. Restarting IB Gateway triggers a 2FA
 * push lock (docs/ib-gateway-recovery.md), so a demo-trial user reaching any of
 * these is a production outage, not a permissions nit.
 */
const CONTROL_PLANE_ROUTES: ReadonlyArray<{ path: string; params?: Record<string, string> }> = [
  { path: "admin/services" },
  { path: "admin/services/[unit]/[action]", params: { unit: "radon-api", action: "restart" } },
  { path: "admin/ib/restart" },
  { path: "admin/ib/reset-backoff" },
  { path: "admin/stack/restart" },
];
// `preferences` belongs to the same operator allowlist but is not listed here:
// its GET is authenticated and deliberately NOT operator-scoped, and its
// mutations are already asserted by OPERATOR_MUTATION_ROUTES above.

/**
 * Routes whose MUTATING methods must pass `operatorOnly: true` and the durable
 * "C" budget at call time. Reads on the same modules are authenticated but not
 * operator-scoped, which is precisely the distinction a whole-file substring
 * check cannot make.
 */
const OPERATOR_MUTATION_ROUTES = [
  "orders/place",
  "orders/cancel",
  "orders/modify",
  "orders/whatif",
  "blotter",
  "flow-analysis",
  "journal",
  "journal/sync",
  "orders",
  "performance",
  "pi",
  "portfolio",
  "preferences",
] as const;

/** Vite resolves dynamic imports statically, so the module map is globbed once. */
const ROUTE_MODULES = import.meta.glob("../app/api/**/route.{ts,tsx}") as Record<
  string,
  () => Promise<Record<string, unknown>>
>;

async function loadRoute(relativePath: string): Promise<Record<string, unknown>> {
  const loader = ROUTE_MODULES[`../app/api/${relativePath}`];
  if (!loader) throw new Error(`no route module at app/api/${relativePath}`);
  return loader();
}

const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"] as const;
type HttpMethod = (typeof HTTP_METHODS)[number];

type Handler = (req: Request, ctx: unknown) => Promise<Response> | Response;

function exportedHandlers(mod: Record<string, unknown>): Array<[HttpMethod, Handler]> {
  return HTTP_METHODS.filter((m) => typeof mod[m] === "function").map(
    (m) => [m, mod[m] as Handler] as [HttpMethod, Handler],
  );
}

function requestFor(method: HttpMethod, path: string): Request {
  const url = `http://localhost:3000/api/${path.replace(/\[(\w+)\]/g, "x")}`;
  if (method === "GET" || method === "DELETE") return new Request(url, { method });
  return new Request(url, {
    method,
    headers: { "content-type": "application/json" },
    body: "{}",
  });
}

beforeEach(() => {
  vi.resetModules();
  mockRequireRouteAccess.mockReset();
  mockRequireRouteAccess.mockResolvedValue(denyResponse());
  mockRadonFetch.mockReset();
  mockRadonFetch.mockResolvedValue({});
  mockReadOrdersSnapshotFromDb.mockReset();
  mockReadOrdersSnapshotFromDb.mockResolvedValue({ orders: [], executions: [] });
});

afterEach(() => {
  vi.clearAllMocks();
});

async function assertDenied(
  modulePath: string,
  label: string,
  params: Record<string, string> | undefined,
) {
  const mod = await loadRoute(modulePath);
  const handlers = exportedHandlers(mod);
  expect(handlers.length, `${label} exports no HTTP handler`).toBeGreaterThan(0);

  for (const [method, handler] of handlers) {
    mockRequireRouteAccess.mockClear();
    mockRadonFetch.mockClear();
    mockReadOrdersSnapshotFromDb.mockClear();

    const res = await handler(requestFor(method, label), {
      params: Promise.resolve(params ?? {}),
    });

    expect(res, `${label} ${method} returned no Response`).toBeInstanceOf(Response);
    expect([401, 403], `${label} ${method} status`).toContain(res.status);
    expect(
      mockRequireRouteAccess,
      `${label} ${method} never called requireRouteAccess`,
    ).toHaveBeenCalled();
    expect(
      mockRadonFetch,
      `${label} ${method} reached radonFetch after a denied authorization`,
    ).not.toHaveBeenCalled();
    expect(
      mockReadOrdersSnapshotFromDb,
      `${label} ${method} read the orders snapshot after a denied authorization`,
    ).not.toHaveBeenCalled();
  }
}

describe("route authorization runs before privileged work", () => {
  it.each(ROUTES.map((r) => [r.path, r] as const))(
    "/api/%s denies every exported method",
    async (_label, route) => {
      await assertDenied(
        `${route.path}/route.${route.ext ?? "ts"}`,
        route.path,
        route.params,
      );
    },
  );

  it.each(ADMIN_ROUTES)("/api/admin/%s denies every exported method", async (route) => {
    await assertDenied(`admin/${route}/route.ts`, `admin/${route}`, undefined);
  });

  it.each(SHARE_ROUTES)("/api/%s/share denies every exported method", async (route) => {
    await assertDenied(`${route}/share/route.ts`, `${route}/share`, undefined);
  });

  it.each(CONTROL_PLANE_ROUTES.map((r) => [r.path, r] as const))(
    "/api/%s denies every exported method before touching the control plane",
    async (_label, route) => {
      await assertDenied(`${route.path}/route.ts`, route.path, route.params);
    },
  );
});

describe("the destructive control plane is operator-scoped at call time", () => {
  it.each(CONTROL_PLANE_ROUTES.map((r) => [r.path, r] as const))(
    "/api/%s passes operatorOnly on every method",
    async (_label, route) => {
      const mod = await loadRoute(`${route.path}/route.ts`);
      const handlers = exportedHandlers(mod);
      expect(handlers.length, `${route.path} exports no HTTP handler`).toBeGreaterThan(0);

      for (const [method, handler] of handlers) {
        mockRequireRouteAccess.mockClear();
        await handler(requestFor(method, route.path), {
          params: Promise.resolve(route.params ?? {}),
        });
        const options = mockRequireRouteAccess.mock.calls[0]?.[1] as
          | Record<string, unknown>
          | undefined;
        expect(options, `${route.path} ${method} passed no options object`).toBeTruthy();
        expect(options!.operatorOnly, `${route.path} ${method} operatorOnly`).toBe(true);
      }
    },
  );
});

describe("live authorization options are passed, not merely spelled", () => {
  it.each(OPERATOR_MUTATION_ROUTES)(
    "/api/%s scopes its mutations to the operator at call time",
    async (path) => {
      const mod = await loadRoute(`${path}/route.ts`);
      const mutations = exportedHandlers(mod).filter(([method]) => method !== "GET");
      expect(mutations.length, `${path} exports no mutating handler`).toBeGreaterThan(0);

      for (const [method, handler] of mutations) {
        mockRequireRouteAccess.mockClear();
        await handler(requestFor(method, path), { params: Promise.resolve({}) });
        const options = mockRequireRouteAccess.mock.calls[0]?.[1] as
          | Record<string, unknown>
          | undefined;
        expect(options, `${path} ${method} passed no options object`).toBeTruthy();
        expect(options!.operatorOnly, `${path} ${method} operatorOnly`).toBe(true);
        expect(options!.durableRateTier, `${path} ${method} durableRateTier`).toBe("C");
      }
    },
  );

  it.each(ADMIN_ROUTES)("/api/admin/%s requests operator capability at call time", async (route) => {
    const mod = await loadRoute(`admin/${route}/route.ts`);
    const handlers = exportedHandlers(mod);
    expect(handlers.length, `admin/${route} exports no HTTP handler`).toBeGreaterThan(0);
    for (const [method, handler] of handlers) {
      mockRequireRouteAccess.mockClear();
      await handler(requestFor(method, `admin/${route}`), { params: Promise.resolve({}) });
      const options = mockRequireRouteAccess.mock.calls[0]?.[1] as
        | Record<string, unknown>
        | undefined;
      expect(options?.operatorOnly, `admin/${route} ${method}`).toBe(true);
    }
  });
});
